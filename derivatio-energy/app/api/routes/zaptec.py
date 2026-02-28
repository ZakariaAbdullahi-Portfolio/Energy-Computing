"""
app/api/routes/zaptec.py
Endpoints för Zaptec-integration.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.zaptec import get_zaptec_token, get_installations, get_chargers

router = APIRouter(prefix="/api/v1/zaptec", tags=["zaptec"])


class ZaptecCredentials(BaseModel):
    username: str
    password: str


@router.post("/installations")
async def list_installations(creds: ZaptecCredentials):
    token = await get_zaptec_token(creds.username, creds.password)
    if not token:
        raise HTTPException(status_code=401, detail="Zaptec-inloggning misslyckades")
    installations = await get_installations(token)
    return {"installations": installations, "count": len(installations)}


@router.get("/chargers/{installation_id}")
async def list_chargers(installation_id: str, username: str, password: str):
    token = await get_zaptec_token(username, password)
    if not token:
        raise HTTPException(status_code=401, detail="Zaptec-inloggning misslyckades")
    chargers = await get_chargers(token, installation_id)
    return {"chargers": chargers, "count": len(chargers)}


class DispatchRequest(BaseModel):
    installation_id: str
    schedule: list[dict]
    username: str = ""
    password: str = ""


from app.services.dispatcher import dispatch_schedule

@router.post("/dispatch")
async def dispatch(req: DispatchRequest):
    result = await dispatch_schedule(
        schedule=req.schedule,
        installation_id=req.installation_id,
        username=req.username or None,
        password=req.password or None,
    )
    return result
