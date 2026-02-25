from app.db.supabase import supabase
from app.models.tariff import GridTariff
from datetime import date

def get_tariff(operator: str, reference_date: date = None) -> GridTariff:
    """Hämtar gällande tariff för en nätoperatör"""
    if reference_date is None:
        reference_date = date.today()

    result = supabase.table("grid_tariffs")\
        .select("*")\
        .eq("operator", operator)\
        .lte("valid_from", reference_date.isoformat())\
        .execute()

    rows = result.data
    if not rows:
        raise ValueError(f"Ingen tariff hittad för {operator}")

    # Filtrera bort utgångna
    active = [r for r in rows if r["valid_to"] is None or r["valid_to"] >= reference_date.isoformat()]
    if not active:
        raise ValueError(f"Ingen aktiv tariff för {operator}")

    r = active[0]
    return GridTariff(
        id=r["id"],
        operator=r["operator"],
        tariff_name=r["tariff_name"],
        valid_from=r["valid_from"],
        valid_to=r.get("valid_to"),
        base_monthly_fee=r["base_monthly_fee"],
        capacity_fee_kw=r["capacity_fee_kw"],
        peak_fee_kw=r["peak_fee_kw"],
        peak_hours_start=r["peak_hours_start"],
        peak_hours_end=r["peak_hours_end"],
        peak_months=r["peak_months"],
        peak_weekdays_only=r["peak_weekdays_only"],
        peak_calc_method=r["peak_calc_method"],
        energy_fee_peak=r["energy_fee_peak"],
        energy_fee_offpeak=r["energy_fee_offpeak"],
    )

def list_operators() -> list[str]:
    result = supabase.table("grid_tariffs").select("operator").execute()
    return list({r["operator"] for r in result.data})