"""WhatsApp Business (Meta Cloud API) — outbound messaging.

Architecture (consistent with the rest of Secretary):
- Secretary (voice/UI) -> backend -> Meta Graph API -> client's WhatsApp.
- The backend is the only place that talks to Meta; it holds the token.

Cloud API rule: outside the 24h customer-service window you may only send a
PRE-APPROVED TEMPLATE. Free-form text works only inside the 24h window (after the
customer messaged you). send_text tries text; callers should fall back to a
template when Meta rejects with a re-engagement error.

Env vars (set on Railway, never logged):
- WHATSAPP_PHONE_NUMBER_ID
- WHATSAPP_ACCESS_TOKEN
- WHATSAPP_API_VERSION (optional, default v21.0)
"""
from __future__ import annotations

import json as _json
import os
import re
import urllib.request
import urllib.error


def _cfg():
    return (
        os.environ.get("WHATSAPP_PHONE_NUMBER_ID"),
        os.environ.get("WHATSAPP_ACCESS_TOKEN"),
        os.environ.get("WHATSAPP_API_VERSION", "v21.0"),
    )


def is_configured() -> bool:
    pnid, token, _ = _cfg()
    return bool(pnid and token)


def normalize_msisdn(phone: str) -> str:
    """Digits only, no '+' (Cloud API wants e.g. 447911123456)."""
    return re.sub(r"\D", "", phone or "")


def _post(payload: dict) -> tuple[bool, str | None, str | None]:
    """POST to the messages endpoint. Returns (ok, message_id, error)."""
    pnid, token, ver = _cfg()
    if not (pnid and token):
        return (False, None, "WhatsApp není nakonfigurovaný na serveru.")
    url = f"https://graph.facebook.com/{ver}/{pnid}/messages"
    data = _json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = _json.loads(resp.read().decode())
        mid = None
        msgs = body.get("messages")
        if isinstance(msgs, list) and msgs:
            mid = msgs[0].get("id")
        return (True, mid, None)
    except urllib.error.HTTPError as e:
        try:
            err = _json.loads(e.read().decode())
            detail = err.get("error", {}).get("message", str(e.code))
        except Exception:
            detail = str(e.code)
        return (False, None, detail)
    except Exception as e:  # noqa: BLE001
        return (False, None, str(e))


def send_text(to_phone: str, text: str) -> tuple[bool, str | None, str | None]:
    """Send a free-form text message (only valid inside the 24h window)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_msisdn(to_phone),
        "type": "text",
        "text": {"body": text},
    }
    return _post(payload)


def send_template(to_phone: str, template_name: str, lang: str = "cs",
                  body_params: list[str] | None = None) -> tuple[bool, str | None, str | None]:
    """Send a pre-approved template message (valid outside the 24h window)."""
    components = []
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in body_params],
        })
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_msisdn(to_phone),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang},
            **({"components": components} if components else {}),
        },
    }
    return _post(payload)
