from app.core.optimizer import run_simulation
from app.models.simulation import SimulationInput, SimulationResult
from app.db.supabase import supabase

def run_and_store(inp: SimulationInput, organization_id: str, user_id: str) -> SimulationResult:
    # Skapa pending-rad
    row = supabase.table("simulations").insert({
        "organization_id": organization_id,
        "property_id": inp.property.id,
        "created_by": user_id,
        "status": "running",
        "period_start": inp.period_start.isoformat(),
        "period_end": inp.period_end.isoformat(),
        "grid_area": inp.property.grid_area,
        "input_params": inp.model_dump(mode="json")
    }).execute()

    sim_id = row.data[0]["id"]

    try:
        result = run_simulation(inp)

        supabase.table("simulations").update({
            "status": "done",
            "result": result.model_dump(mode="json"),
            "cost_without_derivatio": result.cost_without_derivatio,
            "cost_with_derivatio": result.cost_with_derivatio,
            "savings_total": result.savings_total,
            "savings_pct": result.savings_pct,
            "peak_kw_without": result.peak_kw_without,
            "peak_kw_with": result.peak_kw_with,
        }).eq("id", sim_id).execute()

        return result

    except Exception as e:
        supabase.table("simulations").update({
            "status": "error",
            "result": {"error": str(e)}
        }).eq("id", sim_id).execute()
        raise