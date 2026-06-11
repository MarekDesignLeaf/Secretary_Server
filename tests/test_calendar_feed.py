"""GET /crm/calendar-feed — real implementation (API contract gap report §9, step 1)."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from secretary_clean import create_app


def _bootstrap_logged_in_client(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    company = client.post(
        "/api/v1/bootstrap/first-company",
        json={"legal_name": "Feed Test Ltd"},
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


def test_calendar_feed_requires_auth(monkeypatch):
    monkeypatch.setenv("SECRETARY_CLEAN_JWT_SECRET", "test-secret-for-clean-backend")
    client = TestClient(create_app())
    assert client.get("/api/v1/crm/calendar-feed").status_code == 401


def test_calendar_feed_maps_events_to_android_shape(monkeypatch):
    client, headers = _bootstrap_logged_in_client(monkeypatch)

    crm_client = client.post(
        "/api/v1/crm/clients", headers=headers, json={"name": "Feed Customer"},
    ).json()

    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    event = client.post(
        "/api/v1/calendar/events",
        headers=headers,
        json={
            "title": "Site visit",
            "start_at": tomorrow.isoformat(),
            "end_at": (tomorrow + timedelta(hours=2)).isoformat(),
            "client_id": crm_client["id"],
            "description": "Bring the quote",
        },
    ).json()

    far_future = datetime.now(timezone.utc) + timedelta(days=90)
    client.post(
        "/api/v1/calendar/events",
        headers=headers,
        json={"title": "Out of window", "start_at": far_future.isoformat()},
    )

    feed = client.get("/api/v1/crm/calendar-feed?days=30", headers=headers).json()
    assert len(feed) == 1
    entry = feed[0]

    assert entry["entry_key"] == f"calendar_event:{event['id']}"
    assert entry["entry_type"] == "calendar_event"
    assert entry["source_id"] == event["id"]
    assert entry["title"] == "Site visit"
    assert entry["client_name"] == "Feed Customer"
    assert entry["description"] == "Bring the quote"
    assert entry["planned_date"] == tomorrow.date().isoformat()
    assert entry["planned_start_at"].startswith(str(tomorrow.year))
    assert entry["planned_end_at"] is not None
    assert entry["status"] == "scheduled"

    # Gson on the Android side needs every CalendarFeedEntry key present.
    expected_keys = {
        "entry_key", "entry_type", "source_id", "title", "client_name",
        "job_title", "assigned_user_id", "assigned_to", "is_assigned_to_current",
        "display_mode", "planned_start_at", "planned_end_at", "planned_date",
        "description", "calendar_sync_enabled", "reminder_for_assignee_only",
        "status",
    }
    assert set(entry.keys()) == expected_keys

    wide_feed = client.get("/api/v1/crm/calendar-feed?days=365", headers=headers).json()
    assert {e["title"] for e in wide_feed} == {"Site visit", "Out of window"}
