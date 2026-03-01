"""
app/api/routes/scheduler.py
Endpoints för att trigga och övervaka nattlig schemaläggning.
"""

from fastapi import APIRouter, BackgroundTasks
from datetime import datetime
from app.services.scheduler import run_nightly_scheduler

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])


@router.post("/run")
async def trigger_scheduler(background_tasks: BackgroundTasks):
    """
    Triggar nattlig optimering och dispatch manuellt.
    Används för testning och vid behov av omoptimering.
    """
    background_tasks.add_task(run_nightly_scheduler)
    return {
        "status": "started",
        "message": "Schemaläggaren körs i bakgrunden",
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/run-sync")
async def trigger_scheduler_sync():
    """
    Triggar nattlig optimering synkront — väntar på svar.
    Bra för testning, se exakt vad som hände.
    """
    result = await run_nightly_scheduler()
    return result


@router.get("/status")
async def scheduler_status():
    """Enkel statuskoll — är schedulern konfigurerad?"""
    from app.services.scheduler import CUSTOMERS
    return {
        "status": "ok",
        "active_customers": len(CUSTOMERS),
        "mock_mode": True,  # uppdateras när MOCK_MODE = False i dispatcher
        "next_run": "23:00 varje kväll (Railway cron)",
        "timestamp": datetime.now().isoformat(),
    }
