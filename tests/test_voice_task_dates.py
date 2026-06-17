"""Voice task.create must keep the spoken date (calendar dot fix)."""
from datetime import datetime, timedelta, timezone


def _utc_today():
    """Match the parser, which derives relative dates from UTC now."""
    return datetime.now(timezone.utc).date()

from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Task Dates Ltd"},
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


def _next_weekday(wd: int) -> str:
    today = _utc_today()
    delta = (wd - today.weekday()) % 7 or 7
    return (today + timedelta(days=delta)).isoformat()


def test_voice_task_with_weekday_gets_planned_date(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "vytvoř úkol na úterý posekat trávník"}).json()
    assert out["executed"] is True, out
    expected = _next_weekday(1)  # Tuesday
    assert expected in out["message"]

    tasks = client.get("/api/v1/crm/tasks", headers=headers).json()
    assert len(tasks) == 1
    t = tasks[0]
    # Android calendar dots read plannedDate/plannedStartAt/deadline.
    assert t["plannedDate"] == expected
    assert t["deadline"] == expected


def test_voice_task_with_tomorrow_and_time(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    client.post("/api/v1/voice/execute", headers=headers,
                json={"utterance": "vytvoř úkol zítra v 9 zavolat dodavateli"})
    t = client.get("/api/v1/crm/tasks", headers=headers).json()[0]
    tomorrow = (_utc_today() + timedelta(days=1)).isoformat()
    assert t["plannedDate"] == tomorrow
    assert t["plannedStartAt"].startswith(tomorrow)


def test_voice_task_without_date_still_works(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)
    out = client.post("/api/v1/voice/execute", headers=headers,
                      json={"utterance": "vytvoř úkol objednat mulčovací kůru"}).json()
    assert out["executed"] is True
    t = client.get("/api/v1/crm/tasks", headers=headers).json()[0]
    assert t["plannedDate"] is None and t["deadline"] is None
