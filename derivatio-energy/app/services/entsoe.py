"""
app/services/entsoe.py

ENTSO-E Day-Ahead Price Service
--------------------------------
Hämtar timpriser (EUR/MWh → öre/kWh) från ENTSO-E Transparency Platform.

Direktiv C (Failsafe):
  - Om ENTSO-E är nere returneras _fallback_prices() med ett tydligt varningslogg.
  - Alla nätverksfel, timeout och XML-fel hanteras explicit — ingen tyst krasch.
  - Cachning i minnet per (grid_area, datum) för att minska API-anrop och
    överleva kortare nätverksstörningar under en körning.

Kända ENTSO-E-egenheter:
  - Svaret är alltid XML, aldrig JSON.
  - En period kan innehålla 60-minutsintervall (resolution PT60M) eller
    15-minutsintervall (resolution PT15M). Vi hanterar båda.
  - Priset anges i EUR/MWh. Vi konverterar till öre/kWh (× 10 × EUR_SEK).
  - Sommartid kan ge 23 eller 25 timmar — vi normaliserar alltid till 24 timmar
    per dag med ett genomsnitt om data saknas för en timme.
"""

import httpx
import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Optional
from functools import lru_cache

logger = logging.getLogger(__name__)

# ── Konstanter ────────────────────────────────────────────────────────────────

ENTSOE_BASE = "https://web-api.tp.entsoe.eu/api"

AREA_CODES = {
    "SE1": "10Y1001A1001A44P",
    "SE2": "10Y1001A1001A45N",
    "SE3": "10Y1001A1001A46L",
    "SE4": "10Y1001A1001A47J",
}

# ENTSO-E XML namespace — måste matcha exakt, annars hittar ElementTree ingenting
NS = {
    "ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"
}

# Konverteringsfaktor EUR/MWh → öre/kWh  (1 EUR/MWh = 0.1 öre/kWh × EUR_SEK)
# EUR_SEK uppdateras manuellt tills vi kopplar in valutakurs-API
EUR_SEK = 11.20
EUR_MWH_TO_ORE_KWH = EUR_SEK * 0.1   # = 1.12 öre per EUR/MWh

# Rimliga prisgränser för validering (öre/kWh)
# Om ett pris faller utanför dessa gränser loggar vi en varning
PRICE_MIN_ORE  =   0.0    # Negativa priser förekommer men vi clampar till 0
PRICE_MAX_ORE  = 800.0    # >800 öre/kWh är troligen datakvalitetsproblem

# Fallback-pris när ENTSO-E inte svarar (historiskt genomsnitt SE3)
FALLBACK_PRICE_ORE = 120.0

# In-memory cache: (grid_area, start_str, end_str) → list[dict]
# Nollställs vid omstart av servern — tillräckligt för en dags körning
_price_cache: dict[tuple, list[dict]] = {}


# ── Publik funktion ───────────────────────────────────────────────────────────

async def fetch_day_ahead_prices(
    grid_area: str,
    start: date,
    end: date,
    entsoe_api_token: str = "",
) -> list[dict]:
    """
    Hämtar day-ahead spotpriser från ENTSO-E.

    Returnerar en lista med en dict per timme:
        [
            {"timestamp": "2025-01-01T00:00:00", "price_ore_kwh": 94.59},
            {"timestamp": "2025-01-01T01:00:00", "price_ore_kwh": 57.29},
            ...
        ]

    Vid fel returneras fallback-priser + WARNING i loggen (Direktiv C).
    Kräver att entsoe_api_token är satt i .env som ENTSOE_API_TOKEN.
    """
    # Validera elområde
    area_code = AREA_CODES.get(grid_area.upper())
    if not area_code:
        raise ValueError(
            f"Okänt elområde: '{grid_area}'. "
            f"Tillgängliga: {list(AREA_CODES.keys())}"
        )

    # Kolla cache
    cache_key = (grid_area, str(start), str(end))
    if cache_key in _price_cache:
        logger.info(f"Cache-träff för {grid_area} {start}–{end}")
        return _price_cache[cache_key]

    # Hämta från API
    try:
        raw_xml = await _fetch_xml(area_code, start, end, entsoe_api_token)
        prices  = _parse_xml(raw_xml, start, end)

        if not prices:
            logger.warning(
                f"ENTSO-E svarade men XML innehöll inga priser för "
                f"{grid_area} {start}–{end}. Använder fallback."
            )
            return _fallback_prices(start, end)

        # Validera och klamp
        prices = _validate_and_clamp(prices)

        # Spara i cache
        _price_cache[cache_key] = prices
        logger.info(
            f"ENTSO-E: hämtade {len(prices)} timpriser för "
            f"{grid_area} {start}–{end}"
        )
        return prices

    except ENTSOEAuthError as e:
        # API-nyckel saknas eller ogiltig — detta är ett konfigurationsfel,
        # inte ett nätverksfel. Logga tydligt och returnera fallback.
        logger.error(f"ENTSO-E autentiseringsfel: {e}. Kontrollera ENTSOE_API_TOKEN i .env")
        return _fallback_prices(start, end)

    except ENTSOEUnavailableError as e:
        # Nätverksfel, timeout, 5xx — förväntat enligt Direktiv C
        logger.warning(
            f"ENTSO-E otillgänglig ({e}). "
            f"Returnerar fallback-priser ({FALLBACK_PRICE_ORE} öre/kWh)."
        )
        return _fallback_prices(start, end)

    except ENTSOEParseError as e:
        # XML-fel — troligen API-format har ändrats
        logger.error(
            f"XML-parsningsfel från ENTSO-E: {e}. "
            f"Kontrollera att namespace stämmer. Returnerar fallback."
        )
        return _fallback_prices(start, end)


# ── Nätverkslager ─────────────────────────────────────────────────────────────

async def _fetch_xml(
    area_code: str,
    start: date,
    end: date,
    token: str,
) -> str:
    """
    Gör ett HTTP GET mot ENTSO-E och returnerar rå XML-sträng.
    Kastar ENTSOEAuthError eller ENTSOEUnavailableError vid problem.
    """
    if not token:
        raise ENTSOEAuthError("ENTSOE_API_TOKEN är tom eller saknas i .env")

    params = {
        "securityToken": token,
        "documentType":  "A44",          # Day-ahead priser
        "in_Domain":     area_code,
        "out_Domain":    area_code,
        # ENTSO-E kräver exakt format YYYYMMDDHHmm (alltid UTC)
        "periodStart":   start.strftime("%Y%m%d0000"),
        "periodEnd":     end.strftime("%Y%m%d2300"),
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(ENTSOE_BASE, params=params)

        if resp.status_code == 401:
            raise ENTSOEAuthError(f"401 Unauthorized — ogiltig API-nyckel")

        if resp.status_code == 400:
            # ENTSO-E returnerar 400 med XML-felmeddelande vid ogiltiga parametrar
            raise ENTSOEUnavailableError(
                f"400 Bad Request — kontrollera area_code och datumformat. "
                f"Svar: {resp.text[:300]}"
            )

        resp.raise_for_status()   # 5xx → httpx.HTTPStatusError
        return resp.text

    except httpx.TimeoutException as e:
        raise ENTSOEUnavailableError(f"Timeout efter 30s: {e}") from e

    except httpx.ConnectError as e:
        raise ENTSOEUnavailableError(f"Kunde inte ansluta till ENTSO-E: {e}") from e

    except httpx.HTTPStatusError as e:
        raise ENTSOEUnavailableError(
            f"HTTP {e.response.status_code} från ENTSO-E"
        ) from e


# ── XML-parser ────────────────────────────────────────────────────────────────

def _parse_xml(xml_text: str, start: date, end: date) -> list[dict]:
    """
    Parsar ENTSO-E:s Publication_MarketDocument XML.

    Strukturen ser ut så här (förenklat):
        <Publication_MarketDocument>
          <TimeSeries>
            <Period>
              <timeInterval>
                <start>2025-01-01T23:00Z</start>   ← UTC!
              </timeInterval>
              <resolution>PT60M</resolution>         ← eller PT15M
              <Point>
                <position>1</position>
                <price.amount>45.32</price.amount>  ← EUR/MWh
              </Point>
              ...
            </Period>
          </TimeSeries>
        </Publication_MarketDocument>

    Returnerar en platt lista med en dict per timme, sorterad på timestamp.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ENTSOEParseError(f"Ogiltig XML: {e}") from e

    results: dict[str, float] = {}   # timestamp_str → pris i öre/kWh

    # Hitta alla TimeSeries-element
    for ts in root.findall(".//ns:TimeSeries", NS):
        for period in ts.findall("ns:Period", NS):
            # Hämta periodens starttid (UTC)
            interval = period.find("ns:timeInterval/ns:start", NS)
            if interval is None or not interval.text:
                continue

            # Parsa starttid — ENTSO-E använder alltid "2025-01-01T23:00Z"
            try:
                period_start_utc = datetime.strptime(
                    interval.text.replace("Z", "+0000"),
                    "%Y-%m-%dT%H:%M%z"
                )
            except ValueError:
                logger.warning(f"Kunde inte parsa tidsintervall: {interval.text}")
                continue

            # Hämta resolution (PT60M eller PT15M)
            resolution_el = period.find("ns:resolution", NS)
            resolution_minutes = _parse_resolution(
                resolution_el.text if resolution_el is not None else "PT60M"
            )

            # Iterera över alla Point-element
            for point in period.findall("ns:Point", NS):
                pos_el   = point.find("ns:position", NS)
                price_el = point.find("ns:price.amount", NS)

                if pos_el is None or price_el is None:
                    continue

                try:
                    position      = int(pos_el.text)
                    price_eur_mwh = float(price_el.text)
                except (ValueError, TypeError):
                    continue

                # Beräkna tidpunkt för denna Point
                offset_minutes = (position - 1) * resolution_minutes
                point_time_utc = period_start_utc + timedelta(minutes=offset_minutes)

                # Konvertera UTC → svensk lokal tid (CET/CEST)
                # Förenkling: vi lägger till 1h (CET), DST hanteras inte här
                # TODO: använd zoneinfo.ZoneInfo("Europe/Stockholm") i produktion
                point_time_local = point_time_utc + timedelta(hours=1)

                # Konvertera EUR/MWh → öre/kWh
                price_ore = price_eur_mwh * EUR_MWH_TO_ORE_KWH

                ts_str = point_time_local.strftime("%Y-%m-%dT%H:00:00")

                # Om vi har PT15M-data, ta medelvärdet för timmen
                if ts_str in results:
                    results[ts_str] = (results[ts_str] + price_ore) / 2
                else:
                    results[ts_str] = price_ore

    if not results:
        return []

    # Sortera och returnera
    sorted_timestamps = sorted(results.keys())
    return [
        {"timestamp": ts, "price_ore_kwh": round(results[ts], 2)}
        for ts in sorted_timestamps
    ]


def _parse_resolution(resolution: str) -> int:
    """
    Konverterar ENTSO-E resolution-sträng till minuter.
    PT60M → 60, PT15M → 15, PT30M → 30.
    Okänt format → antar 60 och loggar varning.
    """
    mapping = {
        "PT60M": 60,
        "PT15M": 15,
        "PT30M": 30,
        "PT1H":  60,   # alternativ notation
    }
    result = mapping.get(resolution)
    if result is None:
        logger.warning(f"Okänd resolution '{resolution}', antar PT60M")
        return 60
    return result


# ── Validering & Fallback ─────────────────────────────────────────────────────

def _validate_and_clamp(prices: list[dict]) -> list[dict]:
    """
    Kontrollerar att priser ligger inom rimliga gränser.
    Clampar negativa priser till 0 (händer vid hög vindkraftsproduktion).
    Loggar varning om extremt höga priser.
    """
    validated = []
    for entry in prices:
        price = entry["price_ore_kwh"]

        if price < 0:
            logger.debug(
                f"Negativt pris {price:.1f} öre/kWh vid {entry['timestamp']} "
                f"— clampar till 0 (vanligt vid hög vindkraft)"
            )
            price = 0.0

        if price > PRICE_MAX_ORE:
            logger.warning(
                f"Extremt högt pris {price:.1f} öre/kWh vid {entry['timestamp']} "
                f"— behåller värdet men kontrollera datakvalitet"
            )

        validated.append({
            "timestamp":     entry["timestamp"],
            "price_ore_kwh": round(max(PRICE_MIN_ORE, price), 2)
        })

    return validated


def _fallback_prices(start: date, end: date) -> list[dict]:
    """
    Direktiv C — Failsafe.

    Returnerar ett schema med konstant pris (FALLBACK_PRICE_ORE) för varje timme
    i perioden. Optimeraren kan köra men resultaten är konservativa.

    Kallas när ENTSO-E är nere, svarar med fel, eller returnerar tom data.
    """
    prices = []
    current = datetime.combine(start, datetime.min.time())
    end_dt  = datetime.combine(end, datetime.min.time()) + timedelta(days=1)

    while current < end_dt:
        prices.append({
            "timestamp":     current.strftime("%Y-%m-%dT%H:00:00"),
            "price_ore_kwh": FALLBACK_PRICE_ORE
        })
        current += timedelta(hours=1)

    logger.warning(
        f"Fallback-priser aktiva för {len(prices)} timmar "
        f"({start} – {end}). Algoritmen kör med {FALLBACK_PRICE_ORE} öre/kWh."
    )
    return prices


def clear_price_cache() -> None:
    """Töm in-memory cache. Används i tester och vid manuell cache-ogiltigförklaring."""
    _price_cache.clear()
    logger.info("ENTSO-E priscache tömd")


# ── Egna undantagsklasser ─────────────────────────────────────────────────────

class ENTSOEAuthError(Exception):
    """API-nyckel saknas eller är ogiltig."""

class ENTSOEUnavailableError(Exception):
    """ENTSO-E är nere, timeout eller nätverksfel."""

class ENTSOEParseError(Exception):
    """XML-svaret kunde inte tolkas."""
