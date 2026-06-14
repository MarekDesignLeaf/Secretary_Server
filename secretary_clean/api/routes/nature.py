"""Photo recognition routes (voice-controlled tools): plants, plant disease,
mushrooms. Multipart image upload -> OpenAI vision -> structured result.

History is kept in-memory per company (best-effort); the primary value is the
spoken_summary the app reads back.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile

from secretary_clean.api.deps import current_user
from secretary_clean.core import nature_recognition as nr
from secretary_clean.core.models import UserAccount

router = APIRouter(tags=["nature"])

# Best-effort in-memory history: {company_id: [entries]} (newest first).
_HISTORY: dict[str, list] = {}


def _record(user: UserAccount, recognition_type: str, result: dict,
            captured_at: str | None, lat, lon):
    entry = {
        "id": len(_HISTORY.get(user.company_id, [])) + 1,
        "recognition_type": recognition_type,
        "recognition_label": result.get("display_name")
        or result.get("top_issue_name") or "",
        "display_name": result.get("display_name") or "",
        "scientific_name": result.get("scientific_name") or "",
        "confidence": float(result.get("score") or result.get("probability")
                            or result.get("health_probability") or 0.0),
        "guidance": result.get("guidance"),
        "database": result.get("database"),
        "captured_at": captured_at,
        "created_at": None,
        "latitude": lat, "longitude": lon,
        "owner_user_id": user.id, "owner_display_name": user.display_name,
        "owner_email": user.email, "photos": [], "result": result,
    }
    _HISTORY.setdefault(user.company_id, []).insert(0, entry)
    return entry


async def _first_image(images: list[UploadFile]) -> bytes:
    return await images[0].read() if images else b""


async def _all_images(images: list[UploadFile], limit: int = 5) -> list[bytes]:
    return [await img.read() for img in (images or [])[:limit]]


def _parse_organs(organs_json: str) -> list[str]:
    import json as _json
    try:
        val = _json.loads(organs_json or "[]")
        if isinstance(val, list):
            return [str(o) for o in val if o]
    except Exception:  # noqa: BLE001
        pass
    return []


@router.post("/plants/identify")
async def identify_plant(
    images: list[UploadFile] = File(...),
    organs_json: str = Form(default="[]"),
    language: str = Form(default="cs"),
    captured_at: str | None = Form(default=None),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    accuracy_meters: float | None = Form(default=None),
    location_source: str | None = Form(default=None),
    user: UserAccount = Depends(current_user),
):
    all_imgs = await _all_images(images)
    result = nr.identify_plant(all_imgs[0] if all_imgs else b"", language,
                               images=all_imgs, organs=_parse_organs(organs_json))
    _record(user, "plant", result, captured_at, latitude, longitude)
    return result


@router.post("/plants/health-assessment")
async def assess_plant_health(
    images: list[UploadFile] = File(...),
    language: str = Form(default="cs"),
    captured_at: str | None = Form(default=None),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    accuracy_meters: float | None = Form(default=None),
    location_source: str | None = Form(default=None),
    user: UserAccount = Depends(current_user),
):
    all_imgs = await _all_images(images)
    result = nr.assess_health(all_imgs[0] if all_imgs else b"", language, images=all_imgs)
    _record(user, "plant_health", result, captured_at, latitude, longitude)
    return result


@router.post("/mushrooms/identify")
async def identify_mushroom(
    images: list[UploadFile] = File(...),
    language: str = Form(default="cs"),
    captured_at: str | None = Form(default=None),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    accuracy_meters: float | None = Form(default=None),
    location_source: str | None = Form(default=None),
    user: UserAccount = Depends(current_user),
):
    # Kindwise mushroom.id benefits from multiple angles (whole/underside/stem).
    image_bytes = [await img.read() for img in images[:5]]
    result = nr.identify_mushroom(image_bytes, language)
    _record(user, "mushroom", result, captured_at, latitude, longitude)
    return result


@router.get("/nature/history")
def nature_history(
    limit: int = 30,
    recognition_type: str | None = None,
    language: str | None = None,
    user: UserAccount = Depends(current_user),
):
    rows = _HISTORY.get(user.company_id, [])
    if recognition_type:
        rows = [r for r in rows if r["recognition_type"] == recognition_type]
    return rows[:max(1, min(limit, 100))]
