"""
app/services/dispatcher.py
Skickar LP-optimerat laddschema till Zaptec-laddare.
Mock-läge aktivt tills riktig Zaptec-installation finns.
"""
from typing import Optional
from app.services.zaptec import get_zaptec_token, get_chargers, set_charger_current

MOCK_MODE = True  # Byt till False när riktig Zaptec-installation finns


async def dispatch_schedule(
    schedule: list[dict],
    installation_id: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> dict:
    """
    Tar ett laddschema [{hour: 0, charger_kw: 11.0}, ...] och
    skickar rätt ström till varje laddare för varje timme.

    I mock-läge simuleras anropen och returnerar vad som hade skickats.
    """
    if MOCK_MODE:
        return _mock_dispatch(schedule, installation_id)

    token = await get_zaptec_token(username, password)
    if not token:
        return {"success": False, "error": "Zaptec-autentisering misslyckades"}

    chargers = await get_chargers(token, installation_id)
    if not chargers:
        return {"success": False, "error": "Inga laddare hittades"}

    results = []
    for slot in schedule:
        hour = slot["hour"]
        kw_per_charger = slot["charger_kw"]
        amperes = kw_per_charger * 1000 / 230  # kW → Ampere (230V enfas)
        amperes = min(32.0, max(0.0, round(amperes, 1)))

        for charger in chargers:
            success = await set_charger_current(token, charger["Id"], amperes)
            results.append({
                "hour": hour,
                "charger_id": charger["Id"],
                "amperes": amperes,
                "success": success,
            })

    return {
        "success": True,
        "dispatched": len(results),
        "schedule": results,
    }


def _mock_dispatch(schedule: list[dict], installation_id: str) -> dict:
    """Simulerar dispatch utan riktig hårdvara."""
    mock_results = []
    for slot in schedule:
        kw = slot.get("charger_kw", 0)
        amperes = min(32.0, max(0.0, round(kw * 1000 / 230, 1)))
        mock_results.append({
            "hour": slot["hour"],
            "charger_id": f"mock-charger-{installation_id[:8]}",
            "amperes": amperes,
            "kw": kw,
            "success": True,
        })

    off_hours = sum(1 for r in mock_results if r["amperes"] == 0)
    peak_hours = sum(1 for r in mock_results if r["amperes"] > 20)

    return {
        "success": True,
        "mock": True,
        "installation_id": installation_id,
        "dispatched": len(mock_results),
        "off_hours": off_hours,
        "peak_charge_hours": peak_hours,
        "schedule": mock_results,
    }
