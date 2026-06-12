"""POST /whatsapp/send (gap report blocker #42)."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import whatsapp as wa


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "WA Test Ltd"},
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


def test_send_requires_auth(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    assert client.post("/api/v1/whatsapp/send", json={}).status_code == 401


def test_send_unconfigured_is_503(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    res = client.post(
        "/api/v1/whatsapp/send", headers=headers,
        json={"to": "+447911123456", "message": "hi"})
    assert res.status_code == 503


def test_send_success_logs_communication(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.setattr(wa, "is_configured", lambda: True)
    monkeypatch.setattr(wa, "send_text", lambda phone, msg: (True, "wamid.test123", None))

    crm_client = client.post(
        "/api/v1/crm/clients", headers=headers,
        json={"name": "WA Customer", "phone": "+44 7911 123456"},
    ).json()

    missing = client.post(
        "/api/v1/whatsapp/send", headers=headers, json={"to": "", "message": ""})
    assert missing.status_code == 400

    res = client.post(
        "/api/v1/whatsapp/send", headers=headers,
        json={"to": "+447911123456", "message": "Faktura INV-001", "target_language": "en"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "sent"
    assert body["wa_message_id"] == "wamid.test123"

    comms = client.get("/api/v1/crm/communications", headers=headers).json()
    assert len(comms) == 1
    # Communication is labelled with the matched client, not the raw number.
    assert comms[0]["client_name"] == "WA Customer"
    assert comms[0]["comm_type"] == "whatsapp"
    assert comms[0]["external_message_id"] == "wamid.test123"
    assert comms[0]["direction"] == "out"
    _ = crm_client


def test_send_provider_failure_is_502(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monkeypatch.setattr(wa, "is_configured", lambda: True)
    monkeypatch.setattr(wa, "send_text", lambda phone, msg: (False, None, "re-engagement required"))
    res = client.post(
        "/api/v1/whatsapp/send", headers=headers,
        json={"to": "+447911123456", "message": "hi"})
    assert res.status_code == 502
