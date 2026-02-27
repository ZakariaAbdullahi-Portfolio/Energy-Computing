"""
app/api/routes/simulation.py

Kopplar ENTSO-E spotpriser till optimeraren innan simulering körs.
Routen ansvarar för att hämta extern data — optimizer.py ska bara optimera.
"""

import logging
from fastapi import APIRouter, HTTPException, Header
from datetime import timedelta

from app.models.simulation import SimulationInput, SimulationResult
from app.services.simulation_service import run_and_store
from app.services.entsoe import fetch_day_ahead_prices
from app.core.optimizer import run_simulation
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/simulation", tags=["simulation"])


async def _enrich_with_spot_prices(inp: SimulationInput) -> SimulationInput:
    """
    Hämtar riktiga ENTSO-E spotpriser och bifogar dem till SimulationInput
    innan optimeraren körs.

    Om ENTSO-E misslyckas returneras inp oförändrad — optimizer.py
    faller då tillbaka till syntetiska priser och sätter data_quality='fallback'.
    Detta är Direktiv C i praktiken: routen försöker, misslyckas tyst,
    optimeraren klarar sig ändå.
    """
    try:
        # ENTSO-E kräver att end-datumet inkluderar sista dagen
        end_inclusive = inp.period_end + timedelta(days=1)

        prices = await fetch_day_ahead_prices(
            grid_area=inp.property.grid_area,
            start=inp.period_start,
            end=end_inclusive,
            entsoe_api_token=settings.entsoe_api_token,
        )

        if prices:
            # Pydantic-modeller är immutable — skapa nytt objekt med spot_prices
            enriched = inp.model_copy(update={"spot_prices": prices})
            logger.info(
                f"ENTSO-E: {len(prices)} spotpriser bifogade för "
                f"{inp.property.grid_area} {inp.period_start}–{inp.period_end}"
            )
            return enriched
        else:
            logger.warning("ENTSO-E returnerade tomma priser — kör med fallback")
            return inp

    except Exception as e:
        # Fånga ALLT — nätverksfel, parsningsfel, konfigurationsfel
        # Logga med full kontext men låt simuleringen fortsätta
        logger.warning(
            f"ENTSO-E-hämtning misslyckades ({type(e).__name__}: {e}). "
            f"Optimeraren kör med syntetiska priser (data_quality=fallback)."
        )
        return inp


@router.post("/run", response_model=SimulationResult)
async def run_sim(inp: SimulationInput):
    """
    Kör simulering utan att spara — för preview och demo.

    Hämtar riktiga ENTSO-E spotpriser automatiskt om möjligt.
    Vid ENTSO-E-avbrott körs simuleringen med fallback-priser.
    """
    try:
        enriched = await _enrich_with_spot_prices(inp)
        return run_simulation(enriched)
    except Exception as e:
        logger.error(f"Simulering misslyckades: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run-and-store", response_model=SimulationResult)
async def run_and_store_sim(
    inp: SimulationInput,
    x_organization_id: str = Header(...),
    x_user_id: str = Header(...),
):
    """
    Kör simulering och sparar resultatet i Supabase.

    Hämtar riktiga ENTSO-E spotpriser automatiskt om möjligt.
    """
    try:
        enriched = await _enrich_with_spot_prices(inp)
        return run_and_store(enriched, x_organization_id, x_user_id)
    except Exception as e:
        logger.error(f"Simulering + lagring misslyckades: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
