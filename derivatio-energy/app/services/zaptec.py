"""
app/services/zaptec.py
Zaptec Cloud API-integration.
Hämtar installationer, laddare och skickar laddscheman.
"""
import httpx
from typing import Optional
from app.config import settings

ZAPTEC_BASE = "https://api.zaptec.com"


async def get_zaptec_token(username: str, password: str) -> Optional[str]:
    """Hämtar OAuth2 access token från Zaptec."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ZAPTEC_BASE}/oauth/token",
            data={
                "grant_type": "password",
                "username": username,
                "password": password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    if resp.status_code == 200:
        return resp.json().get("access_token")
    return None


async def get_installations(token: str) -> list:
    """Hämtar alla installationer kopplade till kontot."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ZAPTEC_BASE}/api/installation",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    if resp.status_code == 200:
        return resp.json().get("Data", [])
    return []


async def get_chargers(token: str, installation_id: str) -> list:
    """Hämtar alla laddare för en installation."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ZAPTEC_BASE}/api/chargers",
            headers={"Authorization": f"Bearer {token}"},
            params={"installationId": installation_id},
            timeout=10,
        )
    if resp.status_code == 200:
        return resp.json().get("Data", [])
    return []


async def set_charger_current(
    token: str, charger_id: str, current_amperes: float
) -> bool:
    """
    Sätter laddström för en specifik laddare (0-32A).
    Används av schemaläggaren för att styra effekt per timme.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ZAPTEC_BASE}/api/chargers/{charger_id}/settings",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "Settings": [
                    {
                        "SettingId": 710,  # AvailableCurrentPhase1
                        "Value": str(current_amperes),
                    }
                ]
            },
            timeout=10,
        )
    return resp.status_code in (200, 204)
