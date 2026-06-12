"""POST /translate — Android contract: {text, target_language} -> {"translated": ...}.

The app calls this before sending WhatsApp when internal language differs from
the customer language; on failure it gracefully falls back to the original
text, so unconfigured translation maps to 503.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user
from secretary_clean.core import translation
from secretary_clean.core.models import UserAccount

router = APIRouter(tags=["translate"])


@router.post("/translate")
def translate_message(
    payload: dict,
    user: UserAccount = Depends(current_user),
):
    text = str(payload.get("text") or payload.get("message") or "").strip()
    target = str(payload.get("target_language") or "").strip()
    if not text or not target:
        raise HTTPException(status_code=422, detail="'text' and 'target_language' are required")
    if not translation.is_configured():
        raise HTTPException(status_code=503, detail="Translation is not configured on the server.")
    ok, out, err = translation.translate_text(
        text, target, str(payload.get("source_language") or "") or None)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Translation failed: {err}")
    return {"translated": out, "target_language": target}
