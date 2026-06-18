"""Phase 2 — durable aliases, learning events, registry export, resolve preview."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company",
                          json={"legal_name": "Learn Ltd",
                                "default_internal_language_code": "cs-CZ"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_intents_export(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = client.get("/api/v1/voice/intents", headers=headers).json()
    codes = {i["intent_code"] for i in out["intents"]}
    assert "client.create" in codes
    impl = {i["intent_code"]: i["is_implemented"] for i in out["intents"]}
    assert impl["client.create"] is True
    assert impl["invoice.from_work_report"] is False


def test_teach_active_alias_and_resolve(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    # Teach a nonsense phrase → an existing command.
    out = client.post("/api/v1/voice/aliases", headers=headers,
                      json={"phrase": "kobliha expres", "answer": "vytvoř klienta"}).json()
    assert out["status"] == "ACTIVE"
    assert out["target_intent"] == "client.create"

    # It now resolves via the alias.
    res = client.post("/api/v1/voice/learning/resolve", headers=headers,
                      json={"utterance": "kobliha expres"}).json()
    assert res["intent"] == "client.create"
    assert res["source"] == "USER_ALIAS"

    # And it appears in the list.
    aliases = client.get("/api/v1/voice/aliases", headers=headers).json()["aliases"]
    assert any(a["normalized_phrase"] == "kobliha expres" for a in aliases)


def test_teach_pending_alias_when_target_not_implemented(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = client.post("/api/v1/voice/aliases", headers=headers,
                      json={"phrase": "zafakturuj to", "answer": "vytvoř fakturu"}).json()
    assert out["target_intent"] == "invoice.from_work_report"
    assert out["status"] == "PENDING"


def test_unknown_target_is_rejected(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    r = client.post("/api/v1/voice/aliases", headers=headers,
                    json={"phrase": "blah", "answer": "akože nic smysluplného xyz"})
    assert r.status_code == 400


def test_remap_and_soft_delete(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    created = client.post("/api/v1/voice/aliases", headers=headers,
                          json={"phrase": "ein", "answer": "vytvoř klienta"}).json()
    alias_id = created["alias"]["id"]

    # Remap to task.create.
    upd = client.put(f"/api/v1/voice/aliases/{alias_id}", headers=headers,
                     json={"target_intent": "task.create"}).json()
    assert upd["alias"]["target_intent"] == "task.create"

    # Soft delete → DISABLED, and it no longer resolves as an alias.
    d = client.delete(f"/api/v1/voice/aliases/{alias_id}", headers=headers).json()
    assert d["status"] == "disabled"
    res = client.post("/api/v1/voice/learning/resolve", headers=headers,
                      json={"utterance": "ein"}).json()
    assert res["source"] != "USER_ALIAS"

    # Re-teaching the same phrase reactivates rather than duplicating.
    again = client.post("/api/v1/voice/aliases", headers=headers,
                        json={"phrase": "ein", "answer": "vytvoř klienta"}).json()
    assert again["alias"]["id"] == alias_id
    assert again["status"] == "ACTIVE"


def test_learning_events_recorded(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    client.post("/api/v1/voice/aliases", headers=headers,
                json={"phrase": "abc def", "answer": "vytvoř úkol"})
    evs = client.get("/api/v1/voice/learning/events", headers=headers).json()["events"]
    assert len(evs) >= 1
    assert any(e["resolution_type"] in ("USER_ALIAS", "PENDING_ALIAS") for e in evs)


def test_tenant_isolation_on_aliases():
    """Enforcement point: find_voice_alias / get_voice_alias are company-scoped,
    so a company-A alias is never resolvable or readable by company B.
    (Bootstrap only creates one company per app instance, so this is verified
    at the repository layer where isolation is actually enforced.)"""
    from datetime import datetime, timezone
    from secretary_clean.core.repository import InMemorySecretaryRepository
    from secretary_clean.core import voice_learning_service as vls

    repo = InMemorySecretaryRepository()
    alias = vls.new_alias("company-A", "user-A", "tajne heslo", "client.create")
    repo.create_voice_alias(alias)

    # Company A resolves it; company B cannot see or resolve it.
    assert repo.find_voice_alias("company-A", "tajne heslo", "user-A") is not None
    assert repo.find_voice_alias("company-B", "tajne heslo", "user-B") is None
    assert repo.get_voice_alias(alias.id, "company-B") is None
    assert repo.list_voice_aliases("company-B") == []
