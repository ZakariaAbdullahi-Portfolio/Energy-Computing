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
from app.services.optimizer import run_optimization  # befintlig LP-motor

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
        # Kör LP-optimering
        optimization_result = await run_optimization(
            property_data=customer["property"],
            fleet_data=customer["fleet"],
            tariff_data=customer["tariff"],
            period_start=period_start,
            period_end=period_end,
        )

        if not optimization_result or "schedule" not in optimization_result:
            return {"customer": name, "status": "error", "error": "Optimering returnerade inget schema"}

        schedule = optimization_result["schedule"]
        savings = optimization_result.get("savings_total", 0)

        # Dispatcha till Zaptec
        dispatch_result = await dispatch_schedule(
            schedule=schedule,
            installation_id=customer["installation_id"],
            username=customer.get("zaptec_username"),
            password=customer.get("zaptec_password"),
        )

        return {
            "customer": name,
            "status": "ok",
            "savings_sek": round(savings, 0),
            "slots_dispatched": dispatch_result.get("dispatched", 0),
            "mock": dispatch_result.get("mock", False),
        }

    except Exception as e:
        logger.error(f"Fel för kund '{name}': {e}")
        return {"customer": name, "status": "error", "error": str(e)}
