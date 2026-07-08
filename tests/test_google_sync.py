"""Two-way Google Calendar reconciliation (core.google_sync) with a fake Google."""
from datetime import datetime, timezone

from secretary_clean.core import google_sync
from secretary_clean.core.models import CalendarEventCreate
from secretary_clean.core.repository import InMemorySecretaryRepository


class FakeGoogle:
    """In-memory stand-in for the Google Calendar REST API."""

    def __init__(self):
        self.events = {}          # gid -> event body
        self._n = 0
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path.split("?")[0]))
        if method == "POST" and path.endswith("/events"):
            self._n += 1
            gid = f"g{self._n}"
            self.events[gid] = dict(body, id=gid, status="confirmed")
            return True, {"id": gid}, None
        if method == "PATCH":
            gid = path.rsplit("/", 1)[1]
            if gid in self.events:
                self.events[gid].update(body or {})
                return True, self.events[gid], None
            return False, {}, "HTTP 404: not found"
        if method == "DELETE":
            gid = path.rsplit("/", 1)[1]
            self.events.pop(gid, None)
            return True, {}, None
        if method == "GET":
            return True, {"items": list(self.events.values())}, None
        return False, {}, "unexpected"

    def add_external(self, gid, summary, start_iso):
        self.events[gid] = {"id": gid, "status": "confirmed", "summary": summary,
                            "start": {"dateTime": start_iso}, "end": {"dateTime": start_iso}}


def _repo_with_event():
    repo = InMemorySecretaryRepository()
    ev = repo.create_calendar_event(
        "c1", CalendarEventCreate(
            title="Schůzka Novák",
            start_at=datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc)))
    return repo, ev


def test_push_create_maps_event():
    repo, ev = _repo_with_event()
    g = FakeGoogle()
    stats = google_sync.reconcile(repo, "c1", "primary", "tok", g)
    assert stats["pushed"] == 1
    assert repo.get_google_mapping("c1", ev.id) is not None
    assert len(g.events) == 1


def test_second_sync_updates_not_duplicates():
    repo, ev = _repo_with_event()
    g = FakeGoogle()
    google_sync.reconcile(repo, "c1", "primary", "tok", g)
    stats = google_sync.reconcile(repo, "c1", "primary", "tok", g)
    assert stats["pushed"] == 0          # already mapped
    assert stats["updated"] == 1         # patched instead
    assert len(g.events) == 1            # no duplicate


def test_backend_delete_propagates_to_google():
    repo, ev = _repo_with_event()
    g = FakeGoogle()
    google_sync.reconcile(repo, "c1", "primary", "tok", g)
    repo.delete_calendar_event(ev.id, "c1")
    stats = google_sync.reconcile(repo, "c1", "primary", "tok", g)
    assert stats["pushed_deleted"] == 1
    assert g.events == {}
    assert repo.get_google_mapping("c1", ev.id) is None


def test_google_only_event_is_pulled_in():
    repo = InMemorySecretaryRepository()
    g = FakeGoogle()
    g.add_external("gext1", "Klientem založená schůzka", "2026-07-20T09:00:00+00:00")
    stats = google_sync.reconcile(repo, "c1", "primary", "tok", g)
    assert stats["pulled"] == 1
    events = repo.list_calendar_events("c1")
    assert any(e.title == "Klientem založená schůzka" for e in events)
    # Idempotent: a second pass does not re-import.
    stats2 = google_sync.reconcile(repo, "c1", "primary", "tok", g)
    assert stats2["pulled"] == 0


def test_google_cancelled_event_deletes_backend():
    repo = InMemorySecretaryRepository()
    g = FakeGoogle()
    g.add_external("gext2", "Zrušená", "2026-07-21T09:00:00+00:00")
    google_sync.reconcile(repo, "c1", "primary", "tok", g)      # import
    imported = repo.list_calendar_events("c1")[0]
    g.events["gext2"]["status"] = "cancelled"                    # cancel in Google
    stats = google_sync.reconcile(repo, "c1", "primary", "tok", g)
    assert stats["pulled_deleted"] == 1
    assert repo.get_calendar_event(imported.id, "c1") is None


def test_api_error_is_recorded_not_swallowed():
    repo, ev = _repo_with_event()

    def failing(method, path, token, body=None):
        if method == "POST":
            return False, {}, "HTTP 403: insufficient scope"
        return True, {"items": []}, None

    stats = google_sync.reconcile(repo, "c1", "primary", "tok", failing)
    assert stats["failed"] == 1
    log = repo.list_google_sync_log("c1")
    assert any(r["status"] == "error" and "403" in (r.get("detail") or "") for r in log)
