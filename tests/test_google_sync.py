"""Google Calendar sync — SAFE one-way push (create-only, idempotent)."""
from datetime import datetime, timezone

from secretary_clean.core import google_sync
from secretary_clean.core.models import CalendarEventCreate
from secretary_clean.core.repository import InMemorySecretaryRepository


class FakeGoogle:
    def __init__(self):
        self.events = {}
        self._n = 0
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path.split("?")[0]))
        if method == "POST" and path.endswith("/events"):
            self._n += 1
            gid = f"g{self._n}"
            self.events[gid] = dict(body, id=gid)
            return True, {"id": gid}, None
        return False, {}, "unexpected"


def _repo_with_event():
    repo = InMemorySecretaryRepository()
    ev = repo.create_calendar_event(
        "c1", CalendarEventCreate(
            title="Schůzka Novák",
            start_at=datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)))
    return repo, ev


def test_push_create_maps_event():
    repo, ev = _repo_with_event()
    g = FakeGoogle()
    stats = google_sync.reconcile(repo, "c1", "primary", "tok", g)
    assert stats["pushed"] == 1
    assert repo.get_google_mapping("c1", ev.id) is not None
    assert len(g.events) == 1


def test_second_sync_is_idempotent_no_duplicate():
    repo, ev = _repo_with_event()
    g = FakeGoogle()
    google_sync.reconcile(repo, "c1", "primary", "tok", g)
    stats = google_sync.reconcile(repo, "c1", "primary", "tok", g)
    # already mapped → skipped, NOT re-created, NOT re-patched
    assert stats["pushed"] == 0
    assert stats["skipped"] == 1
    assert stats["updated"] == 0
    assert len(g.events) == 1
    assert all(m != "PATCH" for m, _ in g.calls)  # no rewrite


def test_rate_limit_stops_the_run():
    repo = InMemorySecretaryRepository()
    for i in range(5):
        repo.create_calendar_event("c1", CalendarEventCreate(
            title=f"E{i}", start_at=datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)))

    def throttled(method, path, token, body=None):
        return False, {}, "HTTP 429: rateLimitExceeded"

    stats = google_sync.reconcile(repo, "c1", "primary", "tok", throttled)
    # stops after the first rate-limit error instead of hammering Google
    assert stats["failed"] == 1
    assert stats["pushed"] == 0


def test_api_error_is_recorded():
    repo, ev = _repo_with_event()

    def failing(method, path, token, body=None):
        return False, {}, "HTTP 403: insufficient scope"

    stats = google_sync.reconcile(repo, "c1", "primary", "tok", failing)
    assert stats["failed"] == 1
    log = repo.list_google_sync_log("c1")
    assert any(r["status"] == "error" and "403" in (r.get("detail") or "") for r in log)
