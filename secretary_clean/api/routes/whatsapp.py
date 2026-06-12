"""WhatsApp router — send, Meta webhook (inbound), language rule.

POST /whatsapp/send — Android sends {"to", "message", "target_language"?}.
When target_language is present the app has already translated; the server
translates only when it is absent (voice / direct API callers).

GET/POST /whatsapp/webhook — Meta Cloud API callback (public, verified via
WHATSAPP_VERIFY_TOKEN). Inbound text messages land in communications with
direction=in and read=false. Webhook carries no tenant context: the company
comes from WHATSAPP_COMPANY_ID, or the single existing company.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core import translation
from secretary_clean.core import whatsapp as wa
from secretary_clean.core.models import Permission, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


def resolve_languages(repository, company_id: str, client_record=None) -> tuple[str | None, str | None, bool, bool]:
    """(internal_lang, customer_lang, auto_out, auto_in) per tenant profile;
    a client's preferred_language_code overrides the tenant customer default."""
    profile = repository.get_tenant_operating_profile(company_id)
    internal = getattr(profile, "default_internal_language_code", None)
    customer = None
    if client_record is not None:
        customer = (client_record.data or {}).get("preferred_language_code")
    customer = customer or getattr(profile, "default_customer_language_code", None)
    auto_out = bool(getattr(profile, "auto_translate_internal_to_customer", True))
    auto_in = bool(getattr(profile, "auto_translate_customer_to_internal", True))
    return internal, customer, auto_out, auto_in


def outbound_text_for(repository, company_id: str, client_record, message: str) -> tuple[str, dict]:
    """Apply the internal→customer language rule to an outbound message."""
    internal, customer, auto_out, _ = resolve_languages(repository, company_id, client_record)
    meta = {"original_text": message, "internal_language": internal,
            "customer_language": customer, "translated": False}
    if (auto_out and customer and not translation.same_language(internal, customer)
            and translation.is_configured()):
        ok, out, err = translation.translate_text(message, customer, internal)
        if ok and out:
            meta["translated"] = True
            return out, meta
        meta["translation_error"] = err
    return message, meta


def find_client_by_phone(repository, company_id: str, phone: str):
    normalized = wa.normalize_msisdn(phone)
    if not normalized:
        return None
    for c in repository.list_crm_records("clients", company_id):
        if c.status == "deleted":
            continue
        if wa.normalize_msisdn((c.data or {}).get("phone") or "") == normalized:
            return c
    return None


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

    client = find_client_by_phone(repository, user.company_id, phone)

    # Language rule: translate only when the caller has not done it already
    # (Android sends target_language after translating client-side).
    lang_meta: dict = {"translated": False, "original_text": message}
    text_to_send = message
    if not payload.get("target_language"):
        text_to_send, lang_meta = outbound_text_for(repository, user.company_id, client, message)

    ok, message_id, error = wa.send_text(phone, text_to_send)
    if not ok:
        raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {error}")

    contact = client.name if client else phone
    client_id = client.id if client else None
    repository.create_crm_record(
        "communications", user.company_id, f"whatsapp - {contact}",
        {"source": "app", "type": "whatsapp", "direction": "out",
         "contact": contact, "client_id": client_id, "phone": phone,
         "note": text_to_send, "wa_message_id": message_id,
         "target_language": payload.get("target_language") or lang_meta.get("customer_language"),
         **({"original_text": lang_meta["original_text"]} if lang_meta.get("translated") else {})})
    repository.log_activity(
        user.company_id, user.id, "communication", client_id or phone, "whatsapp_send",
        f"WhatsApp to {contact}", source_channel="app")
    return {"status": "sent", "wa_message_id": message_id, "to": phone,
            "sent_text": text_to_send, "translated": lang_meta.get("translated", False)}


@router.get("/webhook")
def webhook_verify(request: Request):
    """Meta webhook verification handshake (public)."""
    params = request.query_params
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if (params.get("hub.mode") == "subscribe" and expected
            and params.get("hub.verify_token") == expected):
        return PlainTextResponse(params.get("hub.challenge") or "")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/webhook")
def webhook_receive(
    payload: dict,
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Meta inbound callback (public). Always answers 200 — Meta retries
    non-200 responses aggressively. Deduplicated by wa_message_id."""
    company_id = os.environ.get("WHATSAPP_COMPANY_ID")
    if not company_id:
        ids = repository.list_company_ids()
        company_id = ids[0] if len(ids) == 1 else None
    if not company_id:
        return {"status": "ignored", "reason": "no unambiguous tenant"}

    existing_ids = {(c.data or {}).get("wa_message_id")
                    for c in repository.list_crm_records("communications", company_id)}
    stored = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            profile_names = {
                (ct.get("wa_id") or ""): ((ct.get("profile") or {}).get("name") or "")
                for ct in value.get("contacts") or []
            }
            for msg in value.get("messages") or []:
                mid = msg.get("id")
                if not mid or mid in existing_ids:
                    continue
                text = ((msg.get("text") or {}).get("body") or "").strip()
                if not text:
                    continue  # non-text messages: Phase 2
                wa_from = str(msg.get("from") or "")
                client = find_client_by_phone(repository, company_id, wa_from)
                contact = client.name if client else (profile_names.get(wa_from) or wa_from)
                comm = repository.create_crm_record(
                    "communications", company_id, f"whatsapp - {contact}",
                    {"source": "whatsapp_webhook", "type": "whatsapp", "direction": "in",
                     "contact": contact, "client_id": client.id if client else None,
                     "phone": wa_from, "note": text, "wa_message_id": mid, "read": False})
                # Auto-fill the client's address if the message contains one.
                try:
                    import types as _types
                    from secretary_clean.api.routes.crm_v2 import (
                        autofill_client_address_from_comm as _autofill)
                    _autofill(repository,
                              _types.SimpleNamespace(id=None, company_id=company_id),
                              comm)
                except Exception:  # noqa: BLE001
                    pass
                existing_ids.add(mid)
                stored += 1
    return {"status": "received", "stored": stored}
