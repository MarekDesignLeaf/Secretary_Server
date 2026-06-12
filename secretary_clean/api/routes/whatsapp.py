"""WhatsApp router — POST /whatsapp/send (gap report §9, blocker #42).

Android calls this with {"to": phone, "message": str, "target_language": str?}
and only checks HTTP success; on any error it falls back to opening the
WhatsApp app locally, so failures here must map to clean HTTP errors.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core import whatsapp as wa
from secretary_clean.core.models import Permission, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.post("/send")
def send_whatsapp(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    if not wa.is_configured():
        raise HTTPException(status_code=503, detail="WhatsApp není na serveru nakonfigurovaný.")
    phone = str(payload.get("to") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not phone or not message:
        raise HTTPException(status_code=400, detail="'to' and 'message' are required")

    ok, message_id, error = wa.send_text(phone, message)
    if not ok:
        raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {error}")

    # Label the communication with the client name when the phone matches CRM.
    contact = phone
    client_id = None
    normalized = wa.normalize_msisdn(phone)
    for c in repository.list_crm_records("clients", user.company_id):
        if wa.normalize_msisdn((c.data or {}).get("phone") or "") == normalized and normalized:
            contact = c.name
            client_id = c.id
            break
    repository.create_crm_record(
        "communications", user.company_id, f"whatsapp - {contact}",
        {"source": "app", "type": "whatsapp", "direction": "out",
         "contact": contact, "client_id": client_id, "phone": phone,
         "note": message, "wa_message_id": message_id,
         "target_language": payload.get("target_language")})
    repository.log_activity(
        user.company_id, user.id, "communication", client_id or phone, "whatsapp_send",
        f"WhatsApp to {contact}", source_channel="app")
    return {"status": "sent", "wa_message_id": message_id, "to": phone}
