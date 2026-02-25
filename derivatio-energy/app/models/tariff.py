from pydantic import BaseModel
from typing import Optional
from datetime import date

class GridTariff(BaseModel):
    id: Optional[str] = None
    operator: str
    tariff_name: str
    valid_from: date
    valid_to: Optional[date] = None
    base_monthly_fee: float = 0
    capacity_fee_kw: float        # kr/kW alltid
    peak_fee_kw: float            # kr/kW tillägg höglast
    peak_hours_start: int         # 0-23
    peak_hours_end: int           # 0-23
    peak_months: list[int]        # [11,12,1,2,3]
    peak_weekdays_only: bool = True
    peak_calc_method: str = "single"  # single | avg3 | avg5
    energy_fee_peak: float = 0
    energy_fee_offpeak: float = 0

    def is_peak_hour(self, dt) -> bool:
        """Avgör om ett datetime är höglasttid"""
        if self.peak_weekdays_only and dt.weekday() >= 5:
            return False
        if dt.month not in self.peak_months:
            return False
        return self.peak_hours_start <= dt.hour < self.peak_hours_end