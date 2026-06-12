"""Voice calendar.create: the 'with whom / what title' answer accepts any text."""
from fastapi.testclient import TestClient

from secretary_clean import create_app


def _client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    c = TestClient(create_app())
    company = c.post("/api/v1/bootstrap/first-company", json={"legal_name": "Cal T Ltd"}).json()
    c.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "o@e.com",
        "display_name": "O", "password": "very-secure-password"})
    tok = c.post("/api/v1/auth/login", json={"email": "o@e.com", "password": "very-secure-password"}).json()
    return c, {"Authorization": f"Bearer {tok['access_token']}"}


def _say(c, h, utter, pid=None):
    body = {"utterance": utter}
    if pid:
        body["pending_action_id"] = pid
    return c.post("/api/v1/voice/execute", headers=h, json=body).json()


def test_multi_turn_meeting_accepts_plain_title(monkeypatch):
    c, h = _client(monkeypatch)
    o1 = _say(c, h, "vytvoř schůzku")
    assert o1["status"] == "needs_more_info"
    pid = o1["pending_action_id"]

    o2 = _say(c, h, "zítra v 10", pid)         # answers WHEN
    assert o2["status"] == "needs_more_info"    # now asks title/person
    assert "název" in o2["question"].lower() or "kým" in o2["question"].lower()

    o3 = _say(c, h, "porada týmu", pid)         # plain title, no person
    assert o3["executed"] is True, o3
    assert o3["resolved_intent"] == "calendar.create"
    assert "porada týmu" in o3["message"]

    events = c.get("/api/v1/calendar/events", headers=h).json()
    assert any(e["title"] == "porada týmu" for e in events)


def test_meeting_with_person_answer(monkeypatch):
    c, h = _client(monkeypatch)
    o1 = _say(c, h, "vytvoř schůzku zítra v 9")
    pid = o1["pending_action_id"]
    assert o1["status"] == "needs_more_info"     # asks title/person
    o2 = _say(c, h, "s Petrem", pid)
    assert o2["executed"] is True
    assert "Petr" in o2["message"]


def test_when_answer_is_not_used_as_title(monkeypatch):
    c, h = _client(monkeypatch)
    o1 = _say(c, h, "vytvoř schůzku")
    pid = o1["pending_action_id"]
    o2 = _say(c, h, "v pátek ve 14", pid)        # this is the WHEN, must not become title
    assert o2["status"] == "needs_more_info"     # still needs a title
    o3 = _say(c, h, "kontrola zakázky", pid)
    assert o3["executed"] is True
    assert "kontrola zakázky" in o3["message"]
