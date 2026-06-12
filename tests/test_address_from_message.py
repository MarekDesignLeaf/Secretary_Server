"""Auto-fill + voice-fill client address from message text."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import address_extract as ax


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company", json={"legal_name": "Addr Ltd"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_extractor_uk_postcode_and_house_street():
    assert ax.extract_address("Hi, it's 14 Oxford Road, Kidlington OX5 1AB") \
        .endswith("OX5 1AB")
    assert "Oxford Road" in ax.extract_address("Dobrý den, adresa je 14 Oxford Road OX5 1AB")
    assert ax.extract_address("přijďte na 221b Baker Street") == "221b Baker Street"
    assert ax.extract_address("ok díky") is None


def test_inbound_message_autofills_client_address(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    c = client.post("/api/v1/crm/clients", headers=headers,
                    json={"name": "Pan Novák"}).json()
    assert not c.get("billing_address_line1")

    res = client.post("/api/v1/crm/communications", headers=headers, json={
        "message_summary": "WhatsApp od Pan Novák", "type": "whatsapp",
        "direction": "in", "contact": "Pan Novák",
        "note": "Dobrý den, přijďte na 14 Oxford Road, Kidlington OX5 1AB", "read": False})
    assert res.status_code in (200, 201)
    body = res.json()
    assert body.get("address_filled", "").endswith("OX5 1AB")

    detail = client.get(f"/api/v1/crm/clients/{c['id']}", headers=headers).json()
    assert detail["client"]["billing_address_line1"].endswith("OX5 1AB")


def test_existing_address_not_overwritten(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    c = client.post("/api/v1/crm/clients", headers=headers,
                    json={"name": "Pan Dvořák",
                          "billing_address_line1": "1 Old Street OX1 1AA"}).json()
    res = client.post("/api/v1/crm/communications", headers=headers, json={
        "message_summary": "x", "type": "whatsapp", "direction": "in",
        "contact": "Pan Dvořák", "note": "nová adresa 9 New Road OX9 9ZZ", "read": False}).json()
    assert "address_filled" not in res
    detail = client.get(f"/api/v1/crm/clients/{c['id']}", headers=headers).json()
    assert detail["client"]["billing_address_line1"] == "1 Old Street OX1 1AA"


def test_voice_set_address_from_latest_message(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers, json={"name": "Smith"})
    # An inbound message without auto-match (no address auto-fill triggered earlier
    # because contact differs) — here it matches, so it would auto-fill; use a
    # client whose name matches and confirm the explicit voice command also works.
    client.post("/api/v1/crm/communications", headers=headers, json={
        "message_summary": "x", "type": "whatsapp", "direction": "in",
        "contact": "Smith", "note": "see you at 5 High Street OX2 6DP", "read": False})
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "doplň adresu klientovi Smith"}).json()
    assert out["executed"] is True
    assert out["resolved_intent"] == "client.set_address"
    assert "OX2 6DP" in out["message"]
