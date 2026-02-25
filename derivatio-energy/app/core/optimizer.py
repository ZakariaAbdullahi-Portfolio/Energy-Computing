"""
Derivatio Energy — Hybrid LP-optimerare
Löser: minimera total elkostnad (spot + effekttariff) för EV-laddning
med hänsyn till:
  - Abonnemangsgräns (kW)
  - Höglasttid (nätbolagets definition)
  - Batteriets SOC-krav (bilen måste vara laddad till departure)
  - Fastighetens baslast timme för timme
"""

import numpy as np
import pandas as pd
import pulp
from datetime import datetime
from typing import List, Tuple

from app.models.simulation import SimulationInput, SimulationResult, HourlyResult, CostBreakdown
from app.models.tariff import GridTariff
from app.core.cost_model import calc_total_cost


# ---------------------------------------------------------------------------
# Syntetisk data (ersätts med ENTSO-E + Metry i produktion)
# ---------------------------------------------------------------------------

def _typkurva_baslast(n: int, sub_kw: float) -> np.ndarray:
    """Kontorsbyggnad typkurva normaliserad mot abonnemang"""
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
    """Realistiska syntetiska spotpriser i öre/kWh"""
    base = 85 if area in ("SE3", "SE4") else 60
    prices = np.zeros(n)
    for i in range(n):
        h = i % 24
        if h in (7, 8, 9, 17, 18, 19, 20):
            prices[i] = base + np.random.uniform(30, 150)
        elif 0 <= h <= 4:
            prices[i] = max(0, base + np.random.uniform(-30, 15))
        else:
            prices[i] = base + np.random.uniform(0, 50)
    return prices


# ---------------------------------------------------------------------------
# Baseline: "dum" smart laddning (enbart spotpris, ingen tarifffänsyn)
# ---------------------------------------------------------------------------

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
    Laddar under billigaste spotpristimmar inom tillgängligt fönster.
    Ignorerar effekttariffen — detta är vad konkurrenterna gör.
    """
    ev = np.zeros(n)
    remaining = kwh_needed

    # Tillgängliga timmar (ankomst → avfärd nästa dag)
    window = []
    for i, dt in enumerate(timestamps):
        h = dt.hour
        if arrival_hour <= departure_hour:
            if arrival_hour <= h < departure_hour:
                window.append(i)
        else:  # över midnatt
            if h >= arrival_hour or h < departure_hour:
                window.append(i)

    # Sortera på spotpris
    window_sorted = sorted(window, key=lambda i: spot[i])

    for i in window_sorted:
        if remaining <= 0:
            break
        charge = min(fleet_kw, remaining)
        ev[i] = charge
        remaining -= charge

    return ev


# ---------------------------------------------------------------------------
# Derivatio LP-optimerare
# ---------------------------------------------------------------------------

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
) -> np.ndarray:
    """
    LP-formulering:
    
    Beslutsvariabler:
      x[t]  = EV-laddeffekt timme t  (kW, kontinuerlig 0 ≤ x[t] ≤ fleet_kw)
      p_cap  = max(base[t] + x[t])   approximeras via hjälpvariabel M
      p_peak = max(base[t] + x[t]) under höglasttid, via hjälpvariabel P

    Minimera:
      Σ_t x[t] * spot[t]/100          (energikostnad spot)
    + p_cap  * capacity_fee_kw        (effektavgift alltid)
    + p_peak * peak_fee_kw            (effektavgift höglast)

    Subject to:
      Σ_t x[t] >= kwh_needed          (ladda tillräckligt)
      x[t] = 0 utanför laddningsfönster
      base[t] + x[t] <= subscription_kw  (abonnemangsgräns)
      M >= base[t] + x[t]  ∀t         (definiera p_cap)
      P >= base[t] + x[t]  ∀t (höglast) (definiera p_peak)
    """
    prob = pulp.LpProblem("derivatio_ev_optimizer", pulp.LpMinimize)

    # Beslutsvariabler: laddeffekt per timme
    x = [pulp.LpVariable(f"x_{t}", lowBound=0, upBound=fleet_kw) for t in range(n)]

    # Hjälpvariabler för effekttopp
    M = pulp.LpVariable("p_cap", lowBound=0)   # total topp
    P = pulp.LpVariable("p_peak", lowBound=0)  # höglasttopp

    # Tillgängligt laddningsfönster
    in_window = np.zeros(n, dtype=bool)
    for i, dt in enumerate(timestamps):
        h = dt.hour
        if arrival_hour > departure_hour:  # över midnatt
            if h >= arrival_hour or h < departure_hour:
                in_window[i] = True
        else:
            if arrival_hour <= h < departure_hour:
                in_window[i] = True

    # Lås x[t]=0 utanför fönster
    for t in range(n):
        if not in_window[t]:
            prob += x[t] == 0

    # Måste ladda tillräckligt
    prob += pulp.lpSum(x[t] for t in range(n)) >= kwh_needed

    # Abonnemangsgräns + definiera M och P
    for t, dt in enumerate(timestamps):
        total_t = base_load[t] + x[t]
        prob += total_t <= subscription_kw
        prob += M >= total_t
        if tariff.is_peak_hour(dt):
            prob += P >= total_t

    # Målfunktion
    energy_obj = pulp.lpSum(
        x[t] * (spot[t] / 100.0 + (
            tariff.energy_fee_peak if tariff.is_peak_hour(timestamps[t])
            else tariff.energy_fee_offpeak
        ))
        for t in range(n)
    )
    capacity_obj = M * tariff.capacity_fee_kw
    peak_obj = P * tariff.peak_fee_kw

    prob += energy_obj + capacity_obj + peak_obj

    # Lös
    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    if pulp.LpStatus[prob.status] != "Optimal":
        # Fallback till naiv om LP misslyckas
        return _naive_ev_schedule(n, fleet_kw, kwh_needed, arrival_hour, departure_hour, spot, timestamps)

    return np.array([pulp.value(x[t]) or 0.0 for t in range(n)])


# ---------------------------------------------------------------------------
# Monte Carlo: osäkerhetsanalys
# ---------------------------------------------------------------------------

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
    n_sim: int = 100,
) -> dict:
    """
    Kör N simuleringar med slumpmässig variation i:
    - Ankomsttid ±1h
    - Energibehov ±15%
    - Baslast ±10%
    Returnerar statistik över besparingar
    """
    savings = []
    n = len(timestamps)

    for _ in range(n_sim):
        arr_jitter = arrival_hour + np.random.randint(-1, 2)
        dep_jitter = departure_hour + np.random.randint(-1, 2)
        kwh_jitter = kwh_needed * np.random.uniform(0.85, 1.15)
        base_jitter = base_load * np.random.uniform(0.90, 1.10)

        ev_naive = _naive_ev_schedule(n, fleet_kw, kwh_jitter, arr_jitter, dep_jitter, spot, timestamps)
        ev_lp = _lp_ev_schedule(n, base_jitter, fleet_kw, kwh_jitter, arr_jitter, dep_jitter,
                                 spot, tariff, subscription_kw, timestamps)

        cost_naive = calc_total_cost((base_jitter + ev_naive).tolist(), spot.tolist(), tariff, timestamps)
        cost_lp = calc_total_cost((base_jitter + ev_lp).tolist(), spot.tolist(), tariff, timestamps)

        savings.append(cost_naive["total"] - cost_lp["total"])

    arr = np.array(savings)
    return {
        "mean": round(float(arr.mean()), 0),
        "median": round(float(np.median(arr)), 0),
        "p10": round(float(np.percentile(arr, 10)), 0),
        "p90": round(float(np.percentile(arr, 90)), 0),
        "std": round(float(arr.std()), 0),
        "n_simulations": n_sim,
    }


# ---------------------------------------------------------------------------
# Huvudfunktion
# ---------------------------------------------------------------------------

def run_simulation(inp: SimulationInput) -> SimulationResult:
    period_start = datetime.combine(inp.period_start, datetime.min.time())
    period_end = datetime.combine(inp.period_end, datetime.min.time())
    timestamps = pd.date_range(period_start, period_end, freq="h").to_pydatetime().tolist()
    n = len(timestamps)
    months = max(1, round((period_end - period_start).days / 30))

    # --- Baslast ---
    if inp.base_load_profile and len(inp.base_load_profile) == n:
        base_load = np.array(inp.base_load_profile)
    else:
        base_load = _typkurva_baslast(n, inp.property.subscription_kw)

    # --- Spotpriser ---
    spot = _syntetiska_spotpriser(n, inp.property.grid_area)

    # --- EV-parametrar ---
    fleet = inp.fleet
    kwh_needed = fleet.vehicle_count * fleet.battery_kwh * (1.0 - fleet.avg_soc_on_arrival)
    fleet_kw = fleet.vehicle_count * fleet.charger_kw

    # --- Schemalägg ---
    ev_naive = _naive_ev_schedule(
        n, fleet_kw, kwh_needed,
        fleet.avg_arrival_hour, fleet.avg_departure_hour,
        spot, timestamps
    )

    ev_lp = _lp_ev_schedule(
        n, base_load, fleet_kw, kwh_needed,
        fleet.avg_arrival_hour, fleet.avg_departure_hour,
        spot, inp.tariff, inp.property.subscription_kw, timestamps
    )

    total_naive = base_load + ev_naive
    total_lp = base_load + ev_lp

    # --- Kostnadskalkyl ---
    cost_naive = calc_total_cost(total_naive.tolist(), spot.tolist(), inp.tariff, timestamps, months)
    cost_lp = calc_total_cost(total_lp.tolist(), spot.tolist(), inp.tariff, timestamps, months)

    savings = cost_naive["total"] - cost_lp["total"]
    savings_pct = (savings / cost_naive["total"] * 100) if cost_naive["total"] > 0 else 0.0

    # --- Monte Carlo ---
    mc = _monte_carlo_savings(
        base_load, spot, inp.tariff, fleet_kw, kwh_needed,
        fleet.avg_arrival_hour, fleet.avg_departure_hour,
        inp.property.subscription_kw, timestamps, n_sim=200
    )

    # --- Identifiera värsta toppar vi kapade ---
    daily_reduction = {}
    for i, dt in enumerate(timestamps):
        day = dt.strftime("%Y-%m-%d")
        diff = total_naive[i] - total_lp[i]
        daily_reduction[day] = daily_reduction.get(day, 0.0) + diff
    worst_days = sorted(daily_reduction, key=lambda d: daily_reduction[d], reverse=True)[:5]

    # --- Timdata ---
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
    )