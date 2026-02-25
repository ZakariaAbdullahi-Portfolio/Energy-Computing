from fastapi import APIRouter, HTTPException
from app.services.entsoe import fetch_day_ahead_prices
from datetime import date

router = APIRouter(prefix="/entsoe", tags=["entsoe"])

@router.get("/prices/{grid_area}")
async def get_prices(grid_area: str, start: str, end: str):
    try:
        prices = await fetch_day_ahead_prices(
            grid_area,
            date.fromisoformat(start),
            date.fromisoformat(end)
        )
        return {"grid_area": grid_area, "prices": prices}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))