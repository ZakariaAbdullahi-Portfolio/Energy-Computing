from pydantic import BaseModel
from typing import Optional

class Fleet(BaseModel):
    id: Optional[str] = None
    property_id: Optional[str] = None
    name: str
    vehicle_count: int = 1
    charger_kw: float = 22.0
    avg_arrival_hour: int = 17
    avg_departure_hour: int = 7
    avg_soc_on_arrival: float = 0.20
    battery_kwh: float = 77.0

class Property(BaseModel):
    id: Optional[str] = None
    organization_id: Optional[str] = None
    name: str
    address: Optional[str] = None
    postal_code: Optional[str] = None
    grid_operator: str
    grid_area: str
    subscription_kw: float
    metry_meter_id: Optional[str] = None