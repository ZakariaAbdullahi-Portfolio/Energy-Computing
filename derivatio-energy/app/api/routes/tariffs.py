from fastapi import APIRouter, HTTPException
from app.services.tariff_service import get_tariff, list_operators
from datetime import date

router = APIRouter(prefix="/tariffs", tags=["tariffs"])

@router.get("/operators")
def get_operators():
    return {"operators": list_operators()}

@router.get("/{operator}")
def get_operator_tariff(operator: str, reference_date: str = None):
    try:
        ref = date.fromisoformat(reference_date) if reference_date else date.today()
        return get_tariff(operator, ref)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))