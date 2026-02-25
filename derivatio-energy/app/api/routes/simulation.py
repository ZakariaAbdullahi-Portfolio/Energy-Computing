from fastapi import APIRouter, HTTPException, Header
from app.models.simulation import SimulationInput, SimulationResult
from app.services.simulation_service import run_and_store
from app.core.optimizer import run_simulation

router = APIRouter(prefix="/simulation", tags=["simulation"])

@router.post("/run", response_model=SimulationResult)
async def run_sim(inp: SimulationInput):
    """Kör simulering utan att spara — för preview/demo"""
    try:
        return run_simulation(inp)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/run-and-store", response_model=SimulationResult)
async def run_and_store_sim(
    inp: SimulationInput,
    x_organization_id: str = Header(...),
    x_user_id: str = Header(...)
):
    """Kör simulering och sparar resultatet i Supabase"""
    try:
        return run_and_store(inp, x_organization_id, x_user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))