"""Contact phone/email format validation across all client/contact write paths."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import contact_validation as cval


# ── unit: normalizers ─────────────────────────────────────────────────────────
def test_normalize_phone_rejects_too_short():
    norm, err = cval.normalize_phone("1234")
    assert norm is None and err  # "1234" is NOT a valid phone


def test_normalize_phone_accepts_and_canonicalizes():
    assert cval.normalize_phone("777 123 456") == ("777123456", None)
    assert cval.normalize_phone("+420 777 123 456") == ("+420777123456", None)
    assert cval.normalize_phone("(420) 777-123-456")[0] == "420777123456"


def test_normalize_phone_empty_is_allowed():
    assert cval.normalize_phone("") == (None, None)
    assert cval.normalize_phone(None) == (None, None)


def test_normalize_phone_rejects_letters_and_overlong():
    assert cval.normalize_phone("abcd")[0] is None
    assert cval.normalize_phone("1" * 20)[0] is None


def test_normalize_email():
    assert cval.normalize_email("D@Example.com") == ("d@example.com", None)
    assert cval.normalize_email("notanemail")[0] is None


def test_validate_and_normalize_collects_errors():
    clean, errs = cval.validate_and_normalize({"phone_primary": "1234", "name": "X"})
    assert errs and clean["name"] == "X"
    clean2, errs2 = cval.validate_and_normalize({"phone": "+420777123456"})
    assert errs2 == [] and clean2["phone"] == "+420777123456"


# ── API harness ───────────────────────────────────────────────────────────────
def _bootstrap(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company",
                          json={"legal_name": "Valid Ltd",
                                "default_internal_language_code": "cs-CZ"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_create_client_rejects_bad_phone(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    r = client.post("/api/v1/crm/clients", headers=headers,
                    json={"name": "Špatný", "phone": "1234"})
    assert r.status_code == 422
    # Nothing was stored.
    assert client.get("/api/v1/crm/clients", headers=headers).json() == []


def test_create_client_normalizes_valid_phone(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    r = client.post("/api/v1/crm/clients", headers=headers,
                    json={"name": "Dobrý", "phone": "777 123 456"})
    assert r.status_code == 200
    assert r.json()["phone_primary"] == "777123456"


def test_create_client_rejects_bad_email(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    r = client.post("/api/v1/crm/clients", headers=headers,
                    json={"name": "X", "email_primary": "not-an-email"})
    assert r.status_code == 422


def test_update_client_rejects_bad_phone(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    cid = client.post("/api/v1/crm/clients", headers=headers,
                      json={"name": "X", "phone": "+420777123456"}).json()["id"]
    r = client.put(f"/api/v1/crm/clients/{cid}", headers=headers,
                   json={"phone_primary": "12"})
    assert r.status_code == 422


def test_sync_contacts_drops_bad_phone_keeps_named_contact(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    res = client.post("/api/v1/crm/clients/sync-contacts", headers=headers, json={"contacts": [
        {"name": "Dobrý", "phone": "+420777000111"},
        {"name": "Bez čísla", "phone": "1234"},       # bad phone → imported w/o phone
        {"name": "", "phone": "987"},                  # only a bad phone, no name → dropped
    ]}).json()
    assert res["imported"] == 2          # Dobrý + "Bez čísla" (without the bad phone)
    assert res["invalid"] == 2           # both "1234" and "987" were rejected
    listed = {c["display_name"]: c for c in client.get("/api/v1/crm/clients", headers=headers).json()}
    assert listed["Bez čísla"]["phone_primary"] is None  # malformed number not stored


def _say(client, headers, utterance, pending_id=None):
    body = {"utterance": utterance}
    if pending_id:
        body["pending_action_id"] = pending_id
    return client.post("/api/v1/voice/execute", headers=headers, json=body).json()


def test_lead_create_rejects_bad_contact_phone(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    r = client.post("/api/v1/crm/leads", headers=headers,
                    json={"contact_name": "Zájemce", "contact_phone": "1234"})
    assert r.status_code == 422


def test_lead_to_client_conversion_normalizes_phone(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    lead = client.post("/api/v1/crm/leads", headers=headers,
                       json={"contact_name": "Zájemce", "contact_phone": "777 123 456",
                             "contact_email": "Z@Example.com"}).json()
    conv = client.post(f"/api/v1/crm/leads/{lead['id']}/convert-to-client", headers=headers)
    assert conv.status_code == 200
    body = conv.json()
    assert body["phone_primary"] == "777123456"
    assert body["email_primary"] == "z@example.com"


def test_voice_client_create_needs_only_name(monkeypatch):
    # v2 UX: creating a client by voice needs only a name — no forced phone/
    # address questions. Phone/email validation still guards the REST create
    # (covered by the other tests in this file).
    client, headers = _bootstrap(monkeypatch)
    out = _say(client, headers, "vytvoř klienta Jan Novák")
    assert out["executed"] is True, out
    stored = client.get("/api/v1/crm/clients", headers=headers).json()
    assert any((c.get("display_name") or c.get("name")) == "Jan Novák" for c in stored)
