import numpy as np
from app.models.tariff import GridTariff

def distribute_ev_load(
    base_load: np.ndarray,
    fleet_kw_total: float,
    charge_hours_needed: int,
    available_start: int,
    available_end: int,
    tariff: GridTariff,
    timestamps: list,
    spot_prices: np.ndarray,
    subscription_kw: float
) -> np.ndarray:
    """
    Hybrid-optimerare: fördelar EV-laddning för att minimera effekttoppar
    och spotpris-kostnad utan att överstiga abonnemangsgräns.
    
    Returnerar timvis EV-last i kW (samma längd som base_load).
    """
    n = len(base_load)
    ev_load = np.zeros(n)

    # Identifiera tillgängliga laddtimmar
    available_hours = []
    for i, dt in enumerate(timestamps):
        if available_start <= dt.hour < available_end or available_end < available_start:
            available_hours.append(i)

    if not available_hours:
        return ev_load

    # Beräkna headroom (utrymme innan abonnemangsgräns nås)
    headroom = np.maximum(0, subscription_kw - base_load)

    # Poängsätt varje tillgänglig timme — lägre poäng = bättre
    # Kombinerar spotpris + effektpåverkan + höglasttid-straff
    scores = np.zeros(n)
    for i in available_hours:
        dt = timestamps[i]
        spot_score = spot_prices[i] / 100.0  # normalisera
        peak_penalty = 2.0 if tariff.is_peak_hour(dt) else 0.0
        load_score = base_load[i] / subscription_kw  # undvik redan högt belastade timmar
        scores[i] = spot_score + peak_penalty + load_score

    # Sortera tillgängliga timmar efter poäng (bäst först)
    ranked = sorted(available_hours, key=lambda i: scores[i])

    # Fördela laddning timme för timme, respektera headroom
    remaining_kwh = fleet_kw_total * charge_hours_needed
    for i in ranked:
        if remaining_kwh <= 0:
            break
        max_charge = min(fleet_kw_total, headroom[i], remaining_kwh)
        if max_charge > 0:
            ev_load[i] = max_charge
            remaining_kwh -= max_charge

    return ev_load

def naive_ev_load(
    fleet_kw_total: float,
    charge_hours_needed: int,
    available_start: int,
    n_hours: int,
    timestamps: list,
    spot_prices: np.ndarray
) -> np.ndarray:
    """
    Simulerar 'dum' smart laddning — startar när spotpriset är lägst
    utan hänsyn till effekttariffen. Används som baseline.
    """
    ev_load = np.zeros(n_hours)
    available = [i for i, dt in enumerate(timestamps) if dt.hour >= available_start]
    ranked = sorted(available, key=lambda i: spot_prices[i])
    remaining = float(charge_hours_needed)
    for i in ranked:
        if remaining <= 0:
            break
        ev_load[i] = fleet_kw_total
        remaining -= 1.0
    return ev_load