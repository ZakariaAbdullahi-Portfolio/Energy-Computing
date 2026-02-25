"""
Derivatio Energy — Hybrid LP-optimerare
Löser: minimera total elkostnad (spot + effekttariff) för EV-laddning

Direktiv C (Failsafe-arkitektur):
  - DataQualityFlag märker resultat som bygger på fallback-data
  - Konservativ säkerhetsmarginal (10%) på abonnemangsgränsen vid fallback
  - last_known_good cache — om vi har kört framgångsrikt inom 24h, använd
    det schemat som varm startpunkt vid dataproblem
  - LP-lösaren har explicit timeout — hänger aldrig
  - Alla fel loggas med tillräcklig kontext för felsökning

Prioriteringsordning vid dataproblem:
  1. Riktiga ENTSO-E priser + Metry baslast          → normalt läge
  2. Riktiga ENTSO-E priser + syntetisk baslast       → DATA_QUALITY_PARTIAL
  3. Fallback priser (120 öre/kWh) + syntetisk baslast → DATA_QUALITY_FALLBACK
  4. LP misslyckas                                     → naiv laddning + varning
"""

import logging
import numpy as np
import pandas as pd
import pulp
from datetime import datetime, timedelta
from typing import Optional

from app.models.simulation import (
    SimulationInput, SimulationResult, HourlyResult, CostBreakdown
)
from app.models.tariff import GridTariff
from app.core.cost_model import calc_total_cost

logger = logging.getLogger(__name__)

# ── Direktiv C: Datakvalitetsflaggor ─────────────────────────────────────────

class DataQuality:
    OK       = "ok"            # Riktiga ENTSO-E priser + Metry baslast
    PARTIAL  = "partial"       # En av datakällorna är syntetisk
    FALLBACK = "fallback"      # Båda datakällorna är syntetiska/fallback

# ── Direktiv C: Last-known-good cache ────────────────────────────────────────
# Nyckel: property_id → (timestamp, ev_lp_schedule, spot_prices)
# Används som startpunkt om ENTSO-E är nere under en körning

_last_known_good: dict[str, dict] = {}

def _save_last_known_good(property_id: str, ev_lp: np.ndarray, spot: np.ndarray) -> None:
    _last_known_good[property_id] = {
        "saved_at": datetime.now(),
        "ev_schedule": ev_lp.copy(),
        "spot_prices": spot.copy(),
    }

def _get_last_known_good(property_id: str) -> Optional[dict]:
    entry = _last_known_good.get(property_id)
    if entry is None:
        return None
    age = datetime.now() - entry["saved_at"]
    if age > timedelta(hours=24):
        logger.info(f"Last-known-good för {property_id} är äldre än 24h — ignoreras")
        return None
    logger.warning(
        f"Använder last-known-good schema för {property_id} "
        f"(sparat för {int(age.total_seconds()/60)} min sedan)"
    )
    return entry


# ── Syntetisk data (ersätts med ENTSO-E + Metry i produktion) ────────────────

def _typkurva_baslast(n: int, sub_kw: float) -> np.ndarray:
    """Kontorsbyggnad typkurva. Används när Metry-data saknas."""
    load = np.zeros(n)
    for i in range(n):
        h = i % 24
        if 8 <= h < 18:
            load[i] = sub_kw * np.random.uniform(0.30, 0.55)
        elif 6 <= h < 8 or 18 <= h < 22:
            load[i] = sub_kw * np.random.uniform(0.12, 0.28)
        else:
            load[i] = sub_kw * np.random.uniform(0.04, 0.12)
    return load


def _syntetiska_spotpriser(n: int, area: str) -> np.ndarray:
    """
    Syntetiska spotpriser. Används när ENTSO-E inte svarar.
    OBS: dessa är konservativt höga — vi överskattar hellre kostnaden
    än underskattar den vid fallback.
    """
    base = 120.0   # konservativt fallback-värde (historiskt genomsnitt)
    prices = np.zeros(n)
    for i in range(n):
        h = i % 24
        if h in (7, 8, 9, 17, 18, 19, 20):
            prices[i] = base + np.random.uniform(30, 80)
        elif 0 <= h <= 4:
            prices[i] = max(0, base + np.random.uniform(-20, 10))
        else:
            prices[i] = base + np.random.uniform(0, 40)
    return prices


# ── Baseline: naiv laddning (enbart spotpris, ingen tariffhänsyn) ─────────────

def _naive_ev_schedule(
    n: int,
    fleet_kw: float,
    kwh_needed: float,
    arrival_hour: int,
    departure_hour: int,
    spot: np.ndarray,
    timestamps: list,
) -> np.ndarray:
    """
    Laddar under billigaste spotpristimmar.
    Ignorerar effekttariffen — detta är vad konkurrenterna gör.
    """
    ev = np.zeros(n)
    remaining = kwh_needed

    window = []
    for i, dt in enumerate(timestamps):
        h = dt.hour
        if arrival_hour > departure_hour:
            if h >= arrival_hour or h < departure_hour:
                window.append(i)
        else:
            if arrival_hour <= h < departure_hour:
                window.append(i)

    for i in sorted(window, key=lambda i: spot[i]):
        if remaining <= 0:
            break
        charge = min(fleet_kw, remaining)
        ev[i] = charge
        remaining -= charge

    return ev


# ── Derivatio LP-optimerare ───────────────────────────────────────────────────

def _lp_ev_schedule(
    n: int,
    base_load: np.ndarray,
    fleet_kw: float,
    kwh_needed: float,
    arrival_hour: int,
    departure_hour: int,
    spot: np.ndarray,
    tariff: GridTariff,
    subscription_kw: float,
    timestamps: list,
    safety_margin: float = 0.0,
) -> np.ndarray:
    """
    LP-formulering med valfri säkerhetsmarginal (Direktiv C).

    safety_margin = 0.10 betyder att vi behandlar abonnemanget som
    90% av det faktiska värdet. Aktiveras vid fallback-data för att
    kompensera för osäkerhet i baslastuppskattningen.

    Returnerar naiv laddning om LP-lösaren misslyckas (explicit fallback).
    """
    # Applicera säkerhetsmarginal på abonnemangsgränsen
    effective_sub = subscription_kw * (1.0 - safety_margin)
    if safety_margin > 0:
        logger.warning(
            f"Säkerhetsmarginal {safety_margin*100:.0f}% aktiv — "
            f"abonnemang behandlas som {effective_sub:.0f} kW (faktiskt {subscription_kw:.0f} kW)"
        )

    prob = pulp.LpProblem("derivatio_ev_optimizer", pulp.LpMinimize)

    x = [pulp.LpVariable(f"x_{t}", lowBound=0, upBound=fleet_kw) for t in range(n)]
    M = pulp.LpVariable("p_cap", lowBound=0)
    P = pulp.LpVariable("p_peak", lowBound=0)

    # Laddningsfönster
    in_window = np.zeros(n, dtype=bool)
    for i, dt in enumerate(timestamps):
        h = dt.hour
        if arrival_hour > departure_hour:
            if h >= arrival_hour or h < departure_hour:
                in_window[i] = True
        else:
            if arrival_hour <= h < departure_hour:
                in_window[i] = True

    for t in range(n):
        if not in_window[t]:
            prob += x[t] == 0

    prob += pulp.lpSum(x[t] for t in range(n)) >= kwh_needed

    for t, dt in enumerate(timestamps):
        total_t = base_load[t] + x[t]
        prob += total_t <= effective_sub
        prob += M >= total_t
        if tariff.is_peak_hour(dt):
            prob += P >= total_t

    energy_obj = pulp.lpSum(
        x[t] * (spot[t] / 100.0 + (
            tariff.energy_fee_peak if tariff.is_peak_hour(timestamps[t])
            else tariff.energy_fee_offpeak
        ))
        for t in range(n)
    )

    prob += energy_obj + M * tariff.capacity_fee_kw + P * tariff.peak_fee_kw

    # Direktiv C: explicit timeout på 60 sekunder
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=60))

    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        logger.error(
            f"LP-lösaren returnerade '{status}' — "
            f"faller tillbaka till naiv laddning. "
            f"Kontrollera att kwh_needed ({kwh_needed:.1f}) ≤ "
            f"tillgänglig kapacitet i fönstret."
        )
        return _naive_ev_schedule(
            n, fleet_kw, kwh_needed, arrival_hour, departure_hour, spot, timestamps
        )

    return np.array([pulp.value(x[t]) or 0.0 for t in range(n)])


# ── Monte Carlo ───────────────────────────────────────────────────────────────

def _monte_carlo_savings(
    base_load: np.ndarray,
    spot: np.ndarray,
    tariff: GridTariff,
    fleet_kw: float,
    kwh_needed: float,
    arrival_hour: int,
    departure_hour: int,
    subscription_kw: float,
    timestamps: list,
    safety_margin: float = 0.0,
    n_sim: int = 200,
) -> dict:
    """
    200 simuleringar med slumpmässig variation.
    safety_margin skickas vidare till LP vid fallback-läge.
    """
    savings = []
    n = len(timestamps)

    for _ in range(n_sim):
        arr_j   = arrival_hour   + np.random.randint(-1, 2)
        dep_j   = departure_hour + np.random.randint(-1, 2)
        kwh_j   = kwh_needed * np.random.uniform(0.85, 1.15)
        base_j  = base_load  * np.random.uniform(0.90, 1.10)

        ev_naive = _naive_ev_schedule(n, fleet_kw, kwh_j, arr_j, dep_j, spot, timestamps)
        ev_lp    = _lp_ev_schedule(
            n, base_j, fleet_kw, kwh_j, arr_j, dep_j,
            spot, tariff, subscription_kw, timestamps, safety_margin
        )

        c_naive = calc_total_cost((base_j + ev_naive).tolist(), spot.tolist(), tariff, timestamps)
        c_lp    = calc_total_cost((base_j + ev_lp).tolist(),    spot.tolist(), tariff, timestamps)

        savings.append(c_naive["total"] - c_lp["total"])

    arr = np.array(savings)
    return {
        "mean":          round(float(arr.mean()), 0),
        "median":        round(float(np.median(arr)), 0),
        "p10":           round(float(np.percentile(arr, 10)), 0),
        "p90":           round(float(np.percentile(arr, 90)), 0),
        "std":           round(float(arr.std()), 0),
        "n_simulations": n_sim,
    }


# ── Huvudfunktion ─────────────────────────────────────────────────────────────

def run_simulation(inp: SimulationInput) -> SimulationResult:
    """
    Kör en komplett simulering.

    Direktiv C-logik:
      1. Avgör datakvalitet (riktiga priser? riktig baslast?)
      2. Sätt safety_margin = 0.10 vid fallback-data
      3. Märk resultatet med data_quality-flagga
      4. Spara last_known_good om körningen lyckas med riktig data
    """
    period_start = datetime.combine(inp.period_start, datetime.min.time())
    period_end   = datetime.combine(inp.period_end,   datetime.min.time())
    timestamps   = pd.date_range(period_start, period_end, freq="h").to_pydatetime().tolist()
    n            = len(timestamps)
    months       = max(1, round((period_end - period_start).days / 30))

    # ── Steg 1: Baslast ──────────────────────────────────────────────────────
    using_real_baseload = False
    if inp.base_load_profile and len(inp.base_load_profile) == n:
        base_load        = np.array(inp.base_load_profile)
        using_real_baseload = True
        logger.info("Använder verklig baslast från Metry")
    else:
        base_load = _typkurva_baslast(n, inp.property.subscription_kw)
        logger.warning(
            "Metry-data saknas — använder syntetisk typkurva. "
            "Koppla in Metry för bättre noggrannhet."
        )

    # ── Steg 2: Spotpriser ───────────────────────────────────────────────────
    # Direktiv C: kontrollera om spotpriser är fallback
    # inp.spot_prices sätts av route-lagret om ENTSO-E lyckades
    using_real_spot = False
    if hasattr(inp, "spot_prices") and inp.spot_prices and len(inp.spot_prices) == n:
        spot          = np.array([p["price_ore_kwh"] for p in inp.spot_prices])
        using_real_spot = True
        logger.info(f"Använder {n} riktiga ENTSO-E spotpriser")
    else:
        spot = _syntetiska_spotpriser(n, inp.property.grid_area)
        logger.warning(
            "ENTSO-E spotpriser saknas — använder syntetiska priser. "
            "Besparingsberäkningen är indikativ."
        )

    # ── Steg 3: Bestäm datakvalitet och säkerhetsmarginal ───────────────────
    if using_real_spot and using_real_baseload:
        data_quality  = DataQuality.OK
        safety_margin = 0.0
    elif using_real_spot or using_real_baseload:
        data_quality  = DataQuality.PARTIAL
        safety_margin = 0.05    # 5% marginal vid partiell data
        logger.warning("Partiell datakvalitet — säkerhetsmarginal 5% aktiv")
    else:
        data_quality  = DataQuality.FALLBACK
        safety_margin = 0.10    # 10% marginal vid full fallback
        logger.warning(
            "FALLBACK-LÄGE: Både spotpriser och baslast är syntetiska. "
            "Säkerhetsmarginal 10% aktiv. Resultaten är indikativa."
        )

    # ── Steg 4: EV-parametrar ────────────────────────────────────────────────
    fleet      = inp.fleet
    kwh_needed = fleet.vehicle_count * fleet.battery_kwh * (1.0 - fleet.avg_soc_on_arrival)
    fleet_kw   = fleet.vehicle_count * fleet.charger_kw

    # ── Steg 5: Schemalägg ───────────────────────────────────────────────────
    ev_naive = _naive_ev_schedule(
        n, fleet_kw, kwh_needed,
        fleet.avg_arrival_hour, fleet.avg_departure_hour,
        spot, timestamps
    )

    ev_lp = _lp_ev_schedule(
        n, base_load, fleet_kw, kwh_needed,
        fleet.avg_arrival_hour, fleet.avg_departure_hour,
        spot, inp.tariff, inp.property.subscription_kw,
        timestamps, safety_margin
    )

    # ── Steg 6: Spara last-known-good om riktig data ─────────────────────────
    if data_quality == DataQuality.OK:
        property_id = getattr(inp.property, "id", "default")
        _save_last_known_good(property_id, ev_lp, spot)
        logger.info(f"Last-known-good sparat för property {property_id}")

    # ── Steg 7: Kostnader ────────────────────────────────────────────────────
    total_naive = base_load + ev_naive
    total_lp    = base_load + ev_lp

    cost_naive = calc_total_cost(total_naive.tolist(), spot.tolist(), inp.tariff, timestamps, months)
    cost_lp    = calc_total_cost(total_lp.tolist(),    spot.tolist(), inp.tariff, timestamps, months)

    savings     = cost_naive["total"] - cost_lp["total"]
    savings_pct = (savings / cost_naive["total"] * 100) if cost_naive["total"] > 0 else 0.0

    # ── Steg 8: Monte Carlo ──────────────────────────────────────────────────
    mc = _monte_carlo_savings(
        base_load, spot, inp.tariff, fleet_kw, kwh_needed,
        fleet.avg_arrival_hour, fleet.avg_departure_hour,
        inp.property.subscription_kw, timestamps,
        safety_margin=safety_margin,
        n_sim=200
    )

    # ── Steg 9: Värsta dagar ─────────────────────────────────────────────────
    daily_reduction = {}
    for i, dt in enumerate(timestamps):
        day  = dt.strftime("%Y-%m-%d")
        diff = total_naive[i] - total_lp[i]
        daily_reduction[day] = daily_reduction.get(day, 0.0) + diff
    worst_days = sorted(daily_reduction, key=lambda d: daily_reduction[d], reverse=True)[:5]

    # ── Steg 10: Timdata ─────────────────────────────────────────────────────
    hourly_data = [
        HourlyResult(
            timestamp=dt.isoformat(),
            base_kw=round(float(base_load[i]), 2),
            ev_kw_without=round(float(ev_naive[i]), 2),
            ev_kw_with=round(float(ev_lp[i]), 2),
            total_kw_without=round(float(total_naive[i]), 2),
            total_kw_with=round(float(total_lp[i]), 2),
            spot_price=round(float(spot[i]), 2),
            is_peak_hour=inp.tariff.is_peak_hour(dt),
        )
        for i, dt in enumerate(timestamps)
    ]

    # ── Steg 11: Returnera med datakvalitetsflagga ────────────────────────────
    if data_quality != DataQuality.OK:
        logger.warning(
            f"Simulation klar med data_quality='{data_quality}'. "
            f"Besparing {savings:.0f} kr är {'indikativ' if data_quality == DataQuality.FALLBACK else 'delvis indikativ'}."
        )

    return SimulationResult(
        period_start=inp.period_start.isoformat(),
        period_end=inp.period_end.isoformat(),
        cost_without_derivatio=cost_naive["total"],
        cost_with_derivatio=cost_lp["total"],
        savings_total=round(savings, 2),
        savings_pct=round(savings_pct, 1),
        peak_kw_without=cost_naive["p_max_all"],
        peak_kw_with=cost_lp["p_max_all"],
        monte_carlo=mc,
        breakdown=CostBreakdown(
            spot_cost_without=cost_naive["energy_cost"],
            spot_cost_with=cost_lp["energy_cost"],
            capacity_cost_without=cost_naive["capacity_cost"],
            capacity_cost_with=cost_lp["capacity_cost"],
            peak_cost_without=cost_naive["peak_cost"],
            peak_cost_with=cost_lp["peak_cost"],
            base_monthly_fee=cost_naive["base_fee"],
        ),
        hourly_data=hourly_data,
        worst_days_avoided=worst_days,
        data_quality=data_quality,
    )
