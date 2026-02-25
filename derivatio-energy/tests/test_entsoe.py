"""
tests/test_entsoe.py

Testar ENTSO-E service utan att faktiskt anropa API:et.
Alla externa anrop mockas.
"""

import pytest
from datetime import date
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.entsoe import (
    _parse_xml,
    _fallback_prices,
    _validate_and_clamp,
    _parse_resolution,
    fetch_day_ahead_prices,
    ENTSOEAuthError,
    ENTSOEUnavailableError,
    ENTSOEParseError,
    clear_price_cache,
    FALLBACK_PRICE_ORE,
)

# ── Exempeldata: minimal giltig ENTSO-E XML ──────────────────────────────────
# Baserat på verkligt API-svar (namespace och struktur måste matcha exakt)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
  <mRID>example</mRID>
  <TimeSeries>
    <mRID>1</mRID>
    <Period>
      <timeInterval>
        <start>2025-01-07T23:00Z</start>
        <end>2025-01-08T23:00Z</end>
      </timeInterval>
      <resolution>PT60M</resolution>
      <Point><position>1</position><price.amount>45.32</price.amount></Point>
      <Point><position>2</position><price.amount>38.10</price.amount></Point>
      <Point><position>3</position><price.amount>31.50</price.amount></Point>
      <Point><position>4</position><price.amount>29.80</price.amount></Point>
      <Point><position>5</position><price.amount>28.00</price.amount></Point>
      <Point><position>6</position><price.amount>35.20</price.amount></Point>
      <Point><position>7</position><price.amount>55.40</price.amount></Point>
      <Point><position>8</position><price.amount>72.10</price.amount></Point>
      <Point><position>9</position><price.amount>85.30</price.amount></Point>
      <Point><position>10</position><price.amount>90.20</price.amount></Point>
      <Point><position>11</position><price.amount>88.50</price.amount></Point>
      <Point><position>12</position><price.amount>82.40</price.amount></Point>
      <Point><position>13</position><price.amount>79.60</price.amount></Point>
      <Point><position>14</position><price.amount>76.30</price.amount></Point>
      <Point><position>15</position><price.amount>74.80</price.amount></Point>
      <Point><position>16</position><price.amount>78.90</price.amount></Point>
      <Point><position>17</position><price.amount>95.60</price.amount></Point>
      <Point><position>18</position><price.amount>110.20</price.amount></Point>
      <Point><position>19</position><price.amount>105.40</price.amount></Point>
      <Point><position>20</position><price.amount>98.30</price.amount></Point>
      <Point><position>21</position><price.amount>89.10</price.amount></Point>
      <Point><position>22</position><price.amount>75.60</price.amount></Point>
      <Point><position>23</position><price.amount>62.40</price.amount></Point>
      <Point><position>24</position><price.amount>52.80</price.amount></Point>
    </Period>
  </TimeSeries>
</Publication_MarketDocument>"""


# ── Test: XML-parsing ─────────────────────────────────────────────────────────

class TestParseXML:
    def test_returnerar_24_timmar(self):
        result = _parse_xml(SAMPLE_XML, date(2025, 1, 8), date(2025, 1, 8))
        assert len(result) == 24, f"Förväntade 24 timmar, fick {len(result)}"

    def test_pris_konverteras_korrekt(self):
        """EUR/MWh × 11.20 × 0.1 = öre/kWh"""
        result = _parse_xml(SAMPLE_XML, date(2025, 1, 8), date(2025, 1, 8))
        # Position 1: 45.32 EUR/MWh → 45.32 × 1.12 = 50.76 öre/kWh
        expected = round(45.32 * 1.12, 2)
        assert result[0]["price_ore_kwh"] == expected, \
            f"Pris: förväntade {expected}, fick {result[0]['price_ore_kwh']}"

    def test_timestamp_format(self):
        result = _parse_xml(SAMPLE_XML, date(2025, 1, 8), date(2025, 1, 8))
        ts = result[0]["timestamp"]
        # Ska vara ISO-format utan Z
        assert "T" in ts, f"Timestamp saknar T: {ts}"
        assert ts.endswith(":00"), f"Timestamp slutar inte på :00: {ts}"

    def test_tom_xml_returnerar_tom_lista(self):
        empty_xml = """<?xml version="1.0"?>
        <Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
        </Publication_MarketDocument>"""
        result = _parse_xml(empty_xml, date(2025, 1, 8), date(2025, 1, 8))
        assert result == []

    def test_ogiltig_xml_kastar_parse_error(self):
        with pytest.raises(ENTSOEParseError):
            _parse_xml("detta är inte xml <<>>", date(2025, 1, 8), date(2025, 1, 8))


# ── Test: Validering & Clamp ──────────────────────────────────────────────────

class TestValidateAndClamp:
    def test_negativt_pris_clampar_till_noll(self):
        prices = [{"timestamp": "2025-01-08T03:00:00", "price_ore_kwh": -15.0}]
        result = _validate_and_clamp(prices)
        assert result[0]["price_ore_kwh"] == 0.0

    def test_normalt_pris_forblir_oforandrat(self):
        prices = [{"timestamp": "2025-01-08T12:00:00", "price_ore_kwh": 120.0}]
        result = _validate_and_clamp(prices)
        assert result[0]["price_ore_kwh"] == 120.0

    def test_extremt_hog_pris_behalls_men_loggas(self):
        """Extremt höga priser ska passera — vi stoppar inte riktig data"""
        prices = [{"timestamp": "2025-01-08T17:00:00", "price_ore_kwh": 900.0}]
        result = _validate_and_clamp(prices)
        assert result[0]["price_ore_kwh"] == 900.0


# ── Test: Fallback ────────────────────────────────────────────────────────────

class TestFallbackPrices:
    def test_returnerar_24_timmar_per_dag(self):
        result = _fallback_prices(date(2025, 1, 1), date(2025, 1, 1))
        assert len(result) == 24

    def test_returnerar_korrekt_antal_for_manad(self):
        result = _fallback_prices(date(2025, 1, 1), date(2025, 1, 31))
        assert len(result) == 31 * 24

    def test_anvander_fallback_konstant(self):
        result = _fallback_prices(date(2025, 1, 1), date(2025, 1, 1))
        for entry in result:
            assert entry["price_ore_kwh"] == FALLBACK_PRICE_ORE


# ── Test: Resolution-parsing ──────────────────────────────────────────────────

class TestParseResolution:
    def test_pt60m(self):   assert _parse_resolution("PT60M") == 60
    def test_pt15m(self):   assert _parse_resolution("PT15M") == 15
    def test_pt30m(self):   assert _parse_resolution("PT30M") == 30
    def test_pt1h(self):    assert _parse_resolution("PT1H")  == 60
    def test_okant_antar_60(self): assert _parse_resolution("UNKNOWN") == 60


# ── Test: Fetch (mockat) ──────────────────────────────────────────────────────

class TestFetchDayAheadPrices:
    @pytest.mark.asyncio
    async def test_okant_omrade_kastar_valueerror(self):
        with pytest.raises(ValueError, match="Okänt elområde"):
            await fetch_day_ahead_prices("SE9", date(2025,1,1), date(2025,1,1))

    @pytest.mark.asyncio
    async def test_returnerar_fallback_vid_auth_fel(self):
        """Saknad API-nyckel ska inte krascha — returnera fallback (Direktiv C)"""
        clear_price_cache()
        result = await fetch_day_ahead_prices(
            "SE3", date(2025,1,1), date(2025,1,1),
            entsoe_api_token=""  # Tomt token → ENTSOEAuthError → fallback
        )
        assert len(result) == 24
        assert all(r["price_ore_kwh"] == FALLBACK_PRICE_ORE for r in result)

    @pytest.mark.asyncio
    async def test_returnerar_riktiga_priser_vid_giltig_xml(self):
        """Mockar HTTP-anropet och verifierar att parsing fungerar end-to-end"""
        clear_price_cache()
        with patch("app.services.entsoe._fetch_xml", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = SAMPLE_XML
            result = await fetch_day_ahead_prices(
                "SE3", date(2025,1,8), date(2025,1,8),
                entsoe_api_token="fake-token"
            )
        assert len(result) == 24
        assert result[0]["price_ore_kwh"] > 0

    @pytest.mark.asyncio
    async def test_cache_truff_ger_samma_resultat(self):
        """Andra anropet ska använda cache, inte göra HTTP-anrop"""
        clear_price_cache()
        with patch("app.services.entsoe._fetch_xml", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = SAMPLE_XML
            result1 = await fetch_day_ahead_prices(
                "SE3", date(2025,1,8), date(2025,1,8), entsoe_api_token="fake"
            )
            result2 = await fetch_day_ahead_prices(
                "SE3", date(2025,1,8), date(2025,1,8), entsoe_api_token="fake"
            )
            # Ska bara ha anropat _fetch_xml EN gång
            assert mock_fetch.call_count == 1
        assert result1 == result2
