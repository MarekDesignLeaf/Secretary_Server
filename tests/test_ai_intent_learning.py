"""AI intent fallback + learning when the deterministic parser fails."""
from fastapi.testclient import TestClient

from secretary_clean import create_app
from secretary_clean.core import ai_intent
import secretary_clean.api.routes.voice as voice_routes


def _client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    voice_routes._LEARNED.clear()
    c = TestClient(create_app())
    company = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "AI Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login", json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}


def test_unknown_phrasing_without_ai_is_graceful(monkeypatch):
    c, h = _client(monkeypatch)
    monkeypatch.setattr(ai_intent, "is_configured", lambda: False)
    out = c.post("/api/v1/voice/execute", headers=h,
                 json={"utterance": "ňuňuňu blě blě"}).json()
    assert out["status"] == "error"
    assert "jinak" in out["message"].lower() or "nerozum" in out["message"].lower()


def test_ai_classifies_unknown_phrasing_and_learns(monkeypatch):
    c, h = _client(monkeypatch)
    calls = {"n": 0}

    def fake_classify(utterance, language=None):
        calls["n"] += 1
        return {"intent": "task.list", "entities": {}, "confidence": 0.9}

    monkeypatch.setattr(ai_intent, "is_configured", lambda: True)
    monkeypatch.setattr(ai_intent, "classify", fake_classify)

    # Unknown phrasing the deterministic parser doesn't catch.
    phrase = "wibble wobble zorp blefuju"
    out1 = c.post("/api/v1/voice/execute", headers=h, json={"utterance": phrase}).json()
    assert out1["executed"] is True
    assert out1["resolved_intent"] == "task.list"
    assert calls["n"] == 1

    # Same phrasing again -> served from the learned cache, AI NOT called again.
    out2 = c.post("/api/v1/voice/execute", headers=h, json={"utterance": phrase}).json()
    assert out2["resolved_intent"] == "task.list"
    assert calls["n"] == 1  # no second AI call -> it learned


def test_ai_extracts_entities_for_action(monkeypatch):
    c, h = _client(monkeypatch)
    monkeypatch.setattr(ai_intent, "is_configured", lambda: True)
    monkeypatch.setattr(ai_intent, "classify", lambda u, language=None: {
        "intent": "task.create", "entities": {"title": "objednat mulčovací kůru"},
        "confidence": 0.95})
    out = c.post("/api/v1/voice/execute", headers=h,
                 json={"utterance": "ať nezapomenu objednat mulčovací kůru"}).json()
    assert out["executed"] is True
    assert out["resolved_intent"] == "task.create"
    tasks = c.get("/api/v1/crm/tasks", headers=h).json()
    assert any(t["title"] == "objednat mulčovací kůru" for t in tasks)


def test_classify_catalogue_matches_supported_intents():
    # Catalogue must only reference real executable intents.
    from secretary_clean.core.alias_learning import SUPPORTED_INTENTS
    assert ai_intent.supported() <= SUPPORTED_INTENTS
