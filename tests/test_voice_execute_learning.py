"""Phase 2c — resolver + alias + learning events wired into /voice/execute."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company",
                          json={"legal_name": "Exec Ltd",
                                "default_internal_language_code": "cs-CZ"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def test_active_alias_drives_execution(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    # Teach an alias, then speak the alias phrase to /voice/execute.
    client.post("/api/v1/voice/aliases", headers=headers,
                json={"phrase": "novej zákazník Bob", "answer": "vytvoř klienta"})
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "novej zákazník Bob"}).json()
    # Resolves to client.create and proceeds (asks for the missing name slot or
    # creates) — the key point is it did NOT fail to understand.
    assert out["resolved_intent"] == "client.create" or out["action"] == "client.create"
    assert out["status"] != "error"

    # A learning event of type USER_ALIAS was recorded, and use_count bumped.
    evs = client.get("/api/v1/voice/learning/events", headers=headers).json()["events"]
    assert any(e["resolution_type"] == "USER_ALIAS" for e in evs)
    aliases = client.get("/api/v1/voice/aliases", headers=headers).json()["aliases"]
    assert aliases[0]["use_count"] >= 1


def test_pending_alias_via_execute_does_not_execute(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    client.post("/api/v1/voice/aliases", headers=headers,
                json={"phrase": "zafakturuj zakázku", "answer": "vytvoř fakturu"})
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "zafakturuj zakázku"}).json()
    assert out["executed"] is False
    assert out["action"] == "invoice.from_work_report"


def test_unknown_utterance_opens_dialog_and_records_event(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "qwerty zxcvb plugh"}).json()
    # Phase 3: unknown command → learning dialog (not a flat error).
    assert out["status"] == "needs_more_info"
    assert out["pending_action_id"]
    evs = client.get("/api/v1/voice/learning/events", headers=headers).json()["events"]
    assert any(e["resolution_type"] == "UNKNOWN" for e in evs)


def test_existing_parser_command_still_works_and_logs(monkeypatch):
    client, headers = _bootstrap(monkeypatch)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "vytvoř úkol zavolat dodavateli"}).json()
    assert out["executed"] is True
    assert "úkol" in out["message"].lower()
    evs = client.get("/api/v1/voice/learning/events", headers=headers).json()["events"]
    # Parser-resolved execution is logged too (resolution_type PARSER).
    assert any(e["resolution_type"] == "PARSER" and e["was_executed"] for e in evs)
