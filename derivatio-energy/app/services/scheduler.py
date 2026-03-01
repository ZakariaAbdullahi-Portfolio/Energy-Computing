"""
app/services/scheduler.py

Nattlig schemaläggare — körs automatiskt 23:00 varje kväll.
Hämtar morgondagens spotpriser, kör LP-optimering och
skickar schemat till Zaptec för varje registrerad installation.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from app.services.dispatcher import dispatch_schedule
from app.core.optimizer import run_simulation
from app.models.simulation import SimulationInput  # befintlig LP-motor

logger = logging.getLogger(__name__)


# ─── KUNDREGISTER ─────────────────────────────────────────────────────────────
# Tills Supabase-integration är klar håller vi kunderna här.
# Format: lista av dicts med all info som behövs för en körning.
CUSTOMERS = [
    # Lägg till kunder här när de aktiveras.
    # Exempel:
    # {
    #     "name": "BRF Solgläntan",
    #     "installation_id": "zaptec-installation-uuid-här",
    #     "zaptec_username": "admin@brfsolgläntan.se",
    #     "zaptec_password": "lösenord",
    #     "property": {
    #         "id": "brf-solglantan",
    #         "name": "BRF Solgläntan",
    #         "grid_area": "SE3",
    #         "grid_operator": "ellevio",
    #         "subscription_kw": 150,
    #         "metry_meter_id": None,
    #     },
    #     "fleet": {
    #         "name": "BRF Flotta",
    #         "vehicle_count": 8,
    #         "charger_kw": 11,
    #         "battery_kwh": 77,
    #         "avg_soc_on_arrival": 0.25,
    #         "avg_arrival_hour": 18,
    #         "avg_departure_hour": 8,
    #     },
    #     "tariff": {
    #         "operator": "ellevio",
    #         "tariff_name": "Ellevio_Effekt",
    #         "valid_from": "2024-01-01",
    #         "base_monthly_fee": 365,
    #         "capacity_fee_kw": 59,
    #         "peak_fee_kw": 70,
    #         "peak_hours_start": 6,
    #         "peak_hours_end": 22,
    #         "peak_months": [11, 12, 1, 2, 3],
    #         "peak_weekdays_only": True,
    #         "peak_calc_method": "single",
    #         "energy_fee_peak": 0.071,
    #         "energy_fee_offpeak": 0.038,
    #     },
    # },
]


# ─── HUVUDFUNKTION ─────────────────────────────────────────────────────────────
async def run_nightly_scheduler() -> dict:
    """
    Körs varje natt klockan 23:00.
    Optimerar och dispatchar schema för alla aktiva kunder.
    """
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    period_start = tomorrow.strftime("%Y-%m-%d")
    period_end = tomorrow.strftime("%Y-%m-%d")

    logger.info(f"Nattlig schemaläggare startad — {now.isoformat()}")
    logger.info(f"Optimerar för: {period_start}")

    if not CUSTOMERS:
        logger.info("Inga aktiva kunder — kör i demo-läge")
        return {
            "status": "ok",
            "message": "Inga aktiva kunder konfigurerade ännu",
            "timestamp": now.isoformat(),
            "customers_processed": 0,
        }

    results = []
    for customer in CUSTOMERS:
        result = await _process_customer(customer, period_start, period_end)
        results.append(result)
        logger.info(f"Kund '{customer['name']}': {result['status']}")

    success_count = sum(1 for r in results if r["status"] == "ok")
    logger.info(f"Klart — {success_count}/{len(results)} kunder lyckades")

    return {
        "status": "ok",
        "timestamp": now.isoformat(),
        "period": period_start,
        "customers_processed": len(results),
        "customers_succeeded": success_count,
        "results": results,
    }


async def _process_customer(customer: dict, period_start: str, period_end: str) -> dict:
    """Kör optimering och dispatch för en enskild kund."""
    name = customer["name"]
    try:
        from app.models.property import Property, Fleet
        from app.models.tariff import GridTariff

        inp = SimulationInput(
            property=Property(**customer["property"]),
            fleet=Fleet(**customer["fleet"]),
            tariff=GridTariff(**customer["tariff"]),
            period_start=period_start,
            period_end=period_end,
        )

        result = run_simulation(inp)

        if not result or not result.hourly_data:
            return {"customer": name, "status": "error", "error": "Optimering returnerade inget schema"}

        # Bygg dispatch-schema från hourly_data
        schedule = [
            {"hour": int(h.timestamp[11:13]), "charger_kw": round(h.ev_kw_with, 2)}
            for h in result.hourly_data
        ]

        dispatch_result = await dispatch_schedule(
            schedule=schedule,
            installation_id=customer["installation_id"],
            username=customer.get("zaptec_username"),
            password=customer.get("zaptec_password"),
        )

        return {
            "customer": name,
            "status": "ok",
            "savings_sek": round(result.savings_total, 0),
            "slots_dispatched": dispatch_result.get("dispatched", 0),
            "mock": dispatch_result.get("mock", False),
            "data_quality": result.data_quality,
        }

    except Exception as e:
        logger.error(f"Fel för kund '{name}': {e}")
        return {"customer": name, "status": "error", "error": str(e)}
