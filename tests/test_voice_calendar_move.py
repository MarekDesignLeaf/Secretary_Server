"""Voice: move/reschedule a calendar event to another day."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post("/api/v1/bootstrap/first-company", json={"legal_name": "Cal Ltd"}).json()
    client.post("/api/v1/bootstrap/first-admin", json={
        "company_id": company["id"], "email": "owner@example.com",
        "display_name": "Owner", "password": "very-secure-password"})
    tokens = client.post("/api/v1/auth/login", json={
        "email": "owner@example.com", "password": "very-secure-password"}).json()
    return client, {"Authorization": f"Bearer {tokens['access_token']}"}


def _next_weekday(wd: int):
    today = datetime.now(timezone.utc).date()
    delta = (wd - today.weekday()) % 7 or 7
    return today + timedelta(days=delta)


def test_move_meeting_with_person_to_friday_keeps_time(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    # Existing event on the upcoming Monday at 10:00, declined name in title.
    monday = _next_weekday(0)
    start = datetime(monday.year, monday.month, monday.day, 10, 0, tzinfo=timezone.utc)
    ev = client.post("/api/v1/calendar/events", headers=headers, json={
        "title": "Schůzka s Novák", "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=1)).isoformat()}).json()

    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "přesuň schůzku s Novákem na pátek"}).json()
    assert out["executed"] is True, out
    assert out["resolved_intent"] == "calendar.update"

    friday = _next_weekday(4)
    moved = client.get(f"/api/v1/calendar/events/{ev['id']}", headers=headers).json()
    assert moved["start_at"].startswith(friday.isoformat())
    assert moved["start_at"][11:16] == "10:00"  # original time kept
    # Duration preserved (1h).
    assert moved["end_at"][11:16] == "11:00"


def test_move_without_when_asks_followup(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    monday = _next_weekday(0)
    start = datetime(monday.year, monday.month, monday.day, 9, 0, tzinfo=timezone.utc)
    client.post("/api/v1/calendar/events", headers=headers, json={
        "title": "Schůzka s Dvořák", "start_at": start.isoformat()})
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "přesuň schůzku s Dvořákem"}).json()
    assert out["status"] == "needs_more_info"
    assert "kdy" in out["question"].lower()


def test_move_unknown_meeting_errors(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "přesuň schůzku s Kdokoliv na pátek"}).json()
    assert out["status"] == "error"
    assert "Nenašla" in out["message"]
