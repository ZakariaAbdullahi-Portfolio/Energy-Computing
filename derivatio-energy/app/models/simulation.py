"""
app/models/simulation.py — uppdaterad med spot_prices-fält

Enda ändringen mot tidigare version: SimulationInput får ett valfritt
spot_prices-fält som routen fyller i med ENTSO-E-data innan anrop till optimizer.
Om fältet saknas eller är None kör optimizer.py med syntetiska priser.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date

from app.models.property import Property, Fleet
from app.models.tariff import GridTariff


# ── Input ─────────────────────────────────────────────────────────────────────

class SimulationInput(BaseModel):
    property: Property
    fleet: Fleet
    tariff: GridTariff
    period_start: date
    period_end: date

    # Valfri baslastprofil — om None genereras syntetisk typkurva
    base_load_profile: Optional[List[float]] = None

    # Valfria spotpriser — fylls av routen via ENTSO-E om möjligt
    # Format: [{"timestamp": "2025-01-01T00:00:00", "price_ore_kwh": 94.59}, ...]
    # Om None kör optimizer.py med syntetiska priser (data_quality=fallback)
    spot_prices: Optional[List[dict]] = None

    model_config = {"extra": "ignore"}


# ── Output ────────────────────────────────────────────────────────────────────

class MonteCarloResult(BaseModel):
    mean: float
    median: float
    p10: float
    p90: float
    std: float
    n_simulations: int


class CostBreakdown(BaseModel):
    spot_cost_without: float
    spot_cost_with: float
    capacity_cost_without: float
    capacity_cost_with: float
    peak_cost_without: float
    peak_cost_with: float
    base_monthly_fee: float


class HourlyResult(BaseModel):
    timestamp: str
    base_kw: float
    ev_kw_without: float
    ev_kw_with: float
    total_kw_without: float
    total_kw_with: float
    spot_price: float
    is_peak_hour: bool


class SimulationResult(BaseModel):
    period_start: str
    period_end: str
    cost_without_derivatio: float
    cost_with_derivatio: float
    savings_total: float
    savings_pct: float
    peak_kw_without: float
    peak_kw_with: float
    monte_carlo: MonteCarloResult
    breakdown: CostBreakdown
    hourly_data: List[HourlyResult]
    worst_days_avoided: List[str]

    # Direktiv C — datakvalitetsflagga
    # "ok"      = riktiga ENTSO-E priser + Metry baslast
    # "partial"  = en datakälla är syntetisk
    # "fallback" = båda datakällorna är syntetiska
    data_quality: str = "fallback"
