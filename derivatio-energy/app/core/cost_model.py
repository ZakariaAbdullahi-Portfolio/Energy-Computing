import numpy as np
from typing import List
from app.models.tariff import GridTariff


def calc_peak_power(hourly_kw: List[float], tariff: GridTariff, timestamps: list) -> dict:
    peak_kw = [kw for kw, dt in zip(hourly_kw, timestamps) if tariff.is_peak_hour(dt)]
    all_kw = hourly_kw

    def top_avg(values: list, n: int) -> float:
        if not values:
            return 0.0
        return float(np.mean(sorted(values, reverse=True)[:min(n, len(values))]))

    method = tariff.peak_calc_method
    if method == "avg3":
        p_max_all = top_avg(all_kw, 3)
        p_max_peak = top_avg(peak_kw, 3)
    elif method == "avg5":
        p_max_all = top_avg(all_kw, 5)
        p_max_peak = top_avg(peak_kw, 5)
    else:
        p_max_all = max(all_kw) if all_kw else 0.0
        p_max_peak = max(peak_kw) if peak_kw else 0.0

    return {"p_max_all": round(p_max_all, 3), "p_max_peak": round(p_max_peak, 3)}


def calc_energy_cost(hourly_kw: List[float], spot_prices: List[float],
                     tariff: GridTariff, timestamps: list) -> float:
    total = 0.0
    for kw, spot, dt in zip(hourly_kw, spot_prices, timestamps):
        grid_fee = tariff.energy_fee_peak if tariff.is_peak_hour(dt) else tariff.energy_fee_offpeak
        total += kw * (spot / 100.0 + grid_fee)
    return round(total, 2)


def calc_total_cost(hourly_kw: List[float], spot_prices: List[float],
                    tariff: GridTariff, timestamps: list, months: int = 1) -> dict:
    peaks = calc_peak_power(hourly_kw, tariff, timestamps)
    energy_cost = calc_energy_cost(hourly_kw, spot_prices, tariff, timestamps)
    capacity_cost = peaks["p_max_all"] * tariff.capacity_fee_kw
    peak_cost = peaks["p_max_peak"] * tariff.peak_fee_kw
    base_fee = tariff.base_monthly_fee * months

    return {
        "energy_cost": round(energy_cost, 2),
        "capacity_cost": round(capacity_cost, 2),
        "peak_cost": round(peak_cost, 2),
        "base_fee": round(base_fee, 2),
        "total": round(energy_cost + capacity_cost + peak_cost + base_fee, 2),
        "p_max_all": peaks["p_max_all"],
        "p_max_peak": peaks["p_max_peak"],
    }
