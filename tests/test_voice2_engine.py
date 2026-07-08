"""Voice Engine v2 — new-capability tests: multi-command, read-back verification,
durable AI-alias learning, and enforced confirmation of dangerous intents."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import ai_intent


def _client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    company = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "V2 Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login",
                 json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}


def _exec(c, h, utterance, pid=None):
    body = {"utterance": utterance}
    if pid:
        body["pending_action_id"] = pid
    return c.post("/api/v1/voice/execute", headers=h, json=body).json()


def test_multi_command_one_sentence_creates_two_records(monkeypatch):
    c, h = _client(monkeypatch)
    # Two single-shot intents (no follow-up slots) in one sentence.
    out = _exec(c, h, "vytvoř úkol zavolat dodavateli a vytvoř zakázku plot")
    cmds = out["data"]["commands"]
    assert len(cmds) == 2
    assert cmds[0]["intent"] == "task.create" and cmds[0]["executed"]
    assert cmds[1]["intent"] == "job.create" and cmds[1]["executed"]
    tasks = c.get("/api/v1/crm/tasks", headers=h).json()
    jobs = c.get("/api/v1/crm/jobs", headers=h).json()
    assert len(tasks) >= 1 and len(jobs) >= 1


def test_multi_command_parks_queue_when_first_needs_slot(monkeypatch):
    c, h = _client(monkeypatch)
    # client.create asks for phone/address → the engine parks BUT remembers the
    # queued second command and runs it after the dialog completes.
    out = _exec(c, h, "vytvoř klienta Jan Novák a vytvoř úkol zavolat dodavateli")
    assert out["status"] == "needs_more_info"
    pid = out["pending_action_id"]
    out = _exec(c, h, "+420777123456", pid)        # phone
    if out["status"] == "needs_more_info":
        out = _exec(c, h, "Hlavní 1, Praha", pid)  # address
    # After finishing the client, the queued task ran too.
    tasks = c.get("/api/v1/crm/tasks", headers=h).json()
    assert len(tasks) >= 1


def test_write_is_verified_by_read_back(monkeypatch):
    c, h = _client(monkeypatch)
    out = _exec(c, h, "vytvoř úkol posekat trávník")
    assert out["executed"] is True
    assert out["data"].get("verified") is True
    assert out["data"]["verification"]["checked"]["name"]["ok"] is True


def test_ai_resolved_phrase_becomes_durable_alias(monkeypatch):
    c, h = _client(monkeypatch)
    calls = {"n": 0}

    def fake(utterance, language=None):
        calls["n"] += 1
        return {"intent": "task.list", "entities": {}, "confidence": 0.9}

    monkeypatch.setattr(ai_intent, "is_configured", lambda: True)
    monkeypatch.setattr(ai_intent, "classify", fake)

    phrase = "blaf bluf task overview zorp"
    o1 = _exec(c, h, phrase)
    assert o1["resolved_intent"] == "task.list" and calls["n"] == 1
    # It was persisted as a durable per-user alias (survives restart) — appears
    # in the alias list, and a second call resolves WITHOUT the AI.
    aliases = c.get("/api/v1/voice/aliases", headers=h).json()["aliases"]
    assert any(a["target_intent"] == "task.list" and a["source"] == "ai_learning"
               for a in aliases)
    o2 = _exec(c, h, phrase)
    assert o2["resolved_intent"] == "task.list" and calls["n"] == 1


def test_dangerous_intent_requires_confirmation(monkeypatch):
    c, h = _client(monkeypatch)
    c.post("/api/v1/crm/clients", headers=h,
           json={"name": "Mr Smith", "phone": "+447911123456"})
    out = _exec(c, h, "pošli whatsapp Smith že dorazím")
    # Walk any slot prompts until we hit the confirmation gate.
    for _ in range(3):
        if "confirmation" in out.get("missing_fields", []):
            break
        if out["status"] == "needs_more_info":
            out = _exec(c, h, "Smith", out["pending_action_id"])
    assert out["requires_confirmation"] is True
    assert "confirmation" in out["missing_fields"]
    assert out["executed"] is False   # nothing sent before the yes


def test_multi_command_shared_context_between_clauses(monkeypatch):
    c, h = _client(monkeypatch)
    # Two clauses that both complete in one shot; the batch reports both.
    out = _exec(c, h, "ukaž úkoly a ukaž zakázky")
    cmds = out["data"]["commands"]
    assert len(cmds) == 2
    assert cmds[0]["intent"] == "task.list"
    assert cmds[1]["intent"] == "job.list"
