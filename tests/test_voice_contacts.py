"""Voice contacts: import, find + read-back, bound-operations hint."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company", json={"legal_name": "Contacts Ltd"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_sync_contacts_imports_and_dedupes(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/crm/clients", headers=headers,
                json={"name": "Existing", "phone": "+420777111222"})

    res = client.post("/api/v1/crm/clients/sync-contacts", headers=headers, json={"contacts": [
        {"name": "Pan Novák", "phone": "+420777000111"},
        {"name": "Existing", "phone": "+420777111222"},   # dup by name+phone
        {"name": "", "phone": ""},                         # skipped (empty)
        {"name": "Paní Dvořáková", "phone": "+420777333444", "email": "d@example.com"},
    ]})
    assert res.status_code == 200
    body = res.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    listed = client.get("/api/v1/crm/clients", headers=headers).json()
    names = {c["display_name"] for c in listed}
    assert {"Pan Novák", "Paní Dvořáková", "Existing"} <= names


def test_voice_import_returns_client_action(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "importuj kontakty"}).json()
    assert out["resolved_intent"] == "contacts.import"
    assert out["status"] == "client_action"
    assert out["data"]["client_action"] == "import_contacts"


def test_voice_find_reads_back_details_and_bound_work(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    c = client.post("/api/v1/crm/clients", headers=headers, json={
        "name": "Pan Novák", "phone": "+420777000111",
        "billing_address_line1": "14 Oxford Road OX5 1AB"}).json()
    client.post("/api/v1/crm/tasks", headers=headers,
                json={"title": "Zavolat", "clientId": c["id"], "status": "novy"})

    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "najdi kontakt Novák"}).json()
    assert out["executed"] is True
    assert out["resolved_intent"] == "client.find"
    assert out["data"]["phone"] == "+420777000111"
    assert "OX5 1AB" in out["message"]
    assert out["data"]["open_tasks"] == 1
    assert "zavolej" in out["message"].lower()


def test_voice_find_unknown(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "najdi kontakt Kdokoliv"}).json()
    assert out["status"] == "error"
    assert "nenašla" in out["message"].lower()
