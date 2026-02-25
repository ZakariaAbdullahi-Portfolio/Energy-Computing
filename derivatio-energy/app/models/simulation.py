from pydantic import BaseModel
from typing import Optional
from datetime import date
from app.models.property import Property, Fleet
from app.models.tariff import GridTariff


class SimulationInput(BaseModel):
    property: Property
    fleet: Fleet
    tariff: GridTariff
    period_start: date
    period_end: date
    base_load_profile: Optional[list[float]] = None  # timvärden kW, None = typkurva


class HourlyResult(BaseModel):
    timestamp: str
    base_kw: float
    ev_kw_without: float
    ev_kw_with: float
    total_kw_without: float
    total_kw_with: float
    spot_price: float
    is_peak_hour: bool


class CostBreakdown(BaseModel):
    spot_cost_without: float
    spot_cost_with: float
    capacity_cost_without: float
    capacity_cost_with: float
    peak_cost_without: float
    peak_cost_with: float
    base_monthly_fee: float


class MonteCarloResult(BaseModel):
    mean: float
    median: float
    p10: float       # pessimistiskt scenario
    p90: float       # optimistiskt scenario
    std: float
    n_simulations: int


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
    hourly_data: list[HourlyResult]
    worst_days_avoided: list[str]