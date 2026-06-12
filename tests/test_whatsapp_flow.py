"""WhatsApp inbound webhook + /translate + voice read/reply + language rule."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import translation as tr
from secretary_clean.core import whatsapp as wa


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "WA Flow Ltd",
              "default_internal_language_code": "cs-CZ",
              "default_customer_language_code": "en-GB"},
    ).json()
    client.post(
        "/api/v1/bootstrap/first-admin",
        json={
            "company_id": company["id"],
            "email": "owner@example.com",
            "display_name": "Owner",
            "password": "very-secure-password",
        },
    )
    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "very-secure-password"},
    ).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def _meta_payload(mid, wa_from, text, profile_name="Smoke Customer"):
    return {
        "entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": wa_from, "profile": {"name": profile_name}}],
            "messages": [{"id": mid, "from": wa_from, "type": "text",
                          "text": {"body": text}}],
        }}]}],
    }


def test_webhook_verify_handshake(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "tajny-token")
    client = TestClient(create_app())
    ok = client.get("/api/v1/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "tajny-token",
        "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/api/v1/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "spatny", "hub.challenge": "x"})
    assert bad.status_code == 403


def test_webhook_stores_inbound_and_dedupes(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers,
                json={"name": "SMOKE-Novák", "phone": "+420 777 000 111"})

    res = client.post("/api/v1/whatsapp/webhook",
                      json=_meta_payload("wamid.1", "420777000111", "Dobrý den, kdy přijdete?"))
    assert res.status_code == 200
    assert res.json()["stored"] == 1

    # Retry of the same message id must not duplicate.
    again = client.post("/api/v1/whatsapp/webhook",
                        json=_meta_payload("wamid.1", "420777000111", "Dobrý den, kdy přijdete?"))
    assert again.json()["stored"] == 0

    comms = client.get("/api/v1/crm/communications", headers=headers).json()
    assert len(comms) == 1
    c = comms[0]
    assert c["client_name"] == "SMOKE-Novák"  # matched by normalized phone
    assert c["direction"] == "in"
    assert c["comm_type"] == "whatsapp"
    assert c["message_summary"] == "Dobrý den, kdy přijdete?"


def test_translate_endpoint(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    res = client.post("/api/v1/translate", headers=headers,
                      json={"text": "Přijdu zítra", "target_language": "en"})
    assert res.status_code == 503

    monkeypatch.setattr(tr, "is_configured", lambda: True)
    monkeypatch.setattr(tr, "translate_text",
                        lambda text, target, source=None: (True, f"[{target}] {text}", None))
    res = client.post("/api/v1/translate", headers=headers,
                      json={"text": "Přijdu zítra", "target_language": "en"})
    assert res.status_code == 200
    assert res.json()["translated"] == "[en] Přijdu zítra"

    missing = client.post("/api/v1/translate", headers=headers, json={"text": ""})
    assert missing.status_code == 422


def test_voice_read_messages_marks_read_and_translates(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/whatsapp/webhook",
                json=_meta_payload("wamid.r1", "447911123456", "See you tomorrow at 9", "Mr Smith"))

    monkeypatch.setattr(tr, "is_configured", lambda: True)
    monkeypatch.setattr(tr, "translate_text",
                        lambda text, target, source=None: (True, f"[{target}] {text}", None))

    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "přečti zprávy"}).json()
    assert out["executed"] is True
    assert out["resolved_intent"] == "whatsapp.read"
    # Inbound message is read back translated into the INTERNAL language (cs-CZ).
    assert "[cs-CZ] See you tomorrow at 9" in out["message"]

    # Second read: nothing new (message was marked read).
    out2 = client.post("/api/v1/voice/execute", headers=headers,
                       json={"utterance": "přečti zprávy"}).json()
    assert "Žádné nové zprávy" in out2["message"]


def test_voice_send_applies_customer_language_rule(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers,
                json={"name": "Mr Smith", "phone": "+447911123456"})

    sent = {}
    monkeypatch.setattr(wa, "is_configured", lambda: True)
    monkeypatch.setattr(wa, "send_text",
                        lambda phone, text: (sent.update(phone=phone, text=text)
                                             or (True, "wamid.out1", None)))
    monkeypatch.setattr(tr, "is_configured", lambda: True)
    monkeypatch.setattr(tr, "translate_text",
                        lambda text, target, source=None: (True, f"[{target}] {text}", None))

    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "pošli whatsapp Smith že přijdu zítra v devět"}).json()
    if out["status"] == "needs_more_info":
        # Multi-turn: the engine asks who the recipient is.
        assert "person" in out["missing_fields"]
        out = client.post("/api/v1/voice/execute", headers=headers,
                          json={"utterance": "Smith",
                                "pending_action_id": out["pending_action_id"]}).json()
    assert out["executed"] is True, out
    # Internal cs-CZ, customer default en-GB -> message goes out translated.
    assert sent["text"].startswith("[en-GB] ")
    assert out["data"]["translated"] is True

    comms = client.get("/api/v1/crm/communications", headers=headers).json()
    out_msgs = [c for c in comms if c["direction"] == "out"]
    assert len(out_msgs) == 1
    assert out_msgs[0]["message_summary"].startswith("[en-GB] ")
