"""Two-way Google Calendar reconciliation (backend = source of truth).

Pure of HTTP transport: the caller injects a `gapi(method, path, token, body)`
callable returning (ok: bool, data: dict, err: str|None). That makes the whole
reconciliation unit-testable with a fake Google, and keeps token/PII handling in
one place (the transport never logs secrets).

Reconciliation rules (design G1, pragmatic subset that actually ships):
  push-create : backend event with no mapping        -> create in Google + map
  push-update : backend event already mapped          -> PATCH Google (backend wins)
  push-delete : mapping whose backend event is gone    -> delete in Google + unmap
  pull-import : Google event with no mapping            -> import to backend + map
  pull-delete : Google event cancelled but still mapped -> delete backend + unmap

Backend is the source of truth, so on a genuine edit conflict the backend copy
wins (mapped events are re-pushed). Google-origin events flow in exactly once,
after which the backend owns them.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone

from secretary_clean.core.models import CalendarEventCreate, CalendarEventUpdate


def _event_body(ev) -> dict:
    body = {
        "summary": ev.title or "(bez nazvu)",
        "description": ev.description or "",
        "location": ev.location or "",
    }
    if ev.all_day:
        body["start"] = {"date": ev.start_at.date().isoformat()}
        body["end"] = {"date": (ev.end_at or ev.start_at).date().isoformat()}
    else:
        body["start"] = {"dateTime": ev.start_at.isoformat()}
        end_dt = ev.end_at or (ev.start_at + timedelta(hours=1))
        body["end"] = {"dateTime": end_dt.isoformat()}
    return body


def _parse_dt(node: dict):
    """Return (datetime, all_day) from a Google start/end node."""
    if not node:
        return None, False
    if node.get("date"):
        d = datetime.fromisoformat(node["date"]).replace(tzinfo=timezone.utc)
        return d, True
    dt = node.get("dateTime")
    if dt:
        return datetime.fromisoformat(dt.replace("Z", "+00:00")), False
    return None, False


def reconcile(repository, company_id: str, calendar_id: str, token: str, gapi) -> dict:
    """Run a full two-way sync. `gapi(method, path, token, body=None)` performs one
    Google Calendar REST call. Returns a stats dict; every failure is recorded in
    the google sync log with the Google error, never silently swallowed."""
    cal = urllib.parse.quote(calendar_id)
    stats = {"pushed": 0, "updated": 0, "pushed_deleted": 0,
             "pulled": 0, "pulled_deleted": 0, "skipped": 0, "failed": 0}

    backend_events = {e.id: e for e in repository.list_calendar_events(company_id)}
    mappings = repository.list_google_mappings(company_id)
    by_backend = {m["backend_event_id"]: m["google_event_id"] for m in mappings}
    mapped_google_ids = set(by_backend.values())

    def log(action, status, **kw):
        repository.add_google_sync_log(company_id, kw.pop("direction", "sync"),
                                       action, status, **kw)

    # ── PUSH: backend → Google ────────────────────────────────────────────────
    for eid, ev in backend_events.items():
        gid = by_backend.get(eid)
        if gid is None:
            ok, data, err = gapi("POST", f"/calendars/{cal}/events", token, _event_body(ev))
            if ok and data.get("id"):
                repository.set_google_mapping(company_id, eid, data["id"])
                log("create", "ok", backend_event_id=eid, google_event_id=data["id"],
                    direction="push")
                stats["pushed"] += 1
            else:
                log("create", "error", backend_event_id=eid, detail=err, direction="push")
                stats["failed"] += 1
        else:
            ok, _data, err = gapi("PATCH", f"/calendars/{cal}/events/{gid}", token,
                                  _event_body(ev))
            if ok:
                stats["updated"] += 1
            else:
                log("update", "error", backend_event_id=eid, google_event_id=gid,
                    detail=err, direction="push")
                stats["failed"] += 1

    # ── PUSH-DELETE: mapping without a backend event → delete in Google ────────
    for m in mappings:
        if m["backend_event_id"] not in backend_events:
            gid = m["google_event_id"]
            ok, _data, err = gapi("DELETE", f"/calendars/{cal}/events/{gid}", token)
            # 404/410 (already gone) is acceptable — treat as done.
            if ok or (err and ("404" in err or "410" in err)):
                repository.delete_google_mapping(company_id, m["backend_event_id"])
                log("delete", "ok", google_event_id=gid, direction="push")
                stats["pushed_deleted"] += 1
            else:
                log("delete", "error", google_event_id=gid, detail=err, direction="push")
                stats["failed"] += 1

    # ── PULL: Google → backend ─────────────────────────────────────────────────
    time_min = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    params = urllib.parse.urlencode({
        "singleEvents": "true", "showDeleted": "true", "maxResults": "250",
        "timeMin": time_min, "orderBy": "startTime",
    })
    ok, data, err = gapi("GET", f"/calendars/{cal}/events?{params}", token)
    if not ok:
        log("pull", "error", detail=err, direction="pull")
        stats["failed"] += 1
    else:
        google_to_backend = {m["google_event_id"]: m["backend_event_id"] for m in
                             repository.list_google_mappings(company_id)}
        for item in data.get("items", []):
            gid = item.get("id")
            if not gid:
                continue
            cancelled = item.get("status") == "cancelled"
            if cancelled:
                beid = google_to_backend.get(gid)
                if beid and repository.get_calendar_event(beid, company_id):
                    repository.delete_calendar_event(beid, company_id)
                    repository.delete_google_mapping(company_id, beid)
                    log("delete", "ok", backend_event_id=beid, google_event_id=gid,
                        direction="pull")
                    stats["pulled_deleted"] += 1
                continue
            if gid in mapped_google_ids or gid in google_to_backend:
                stats["skipped"] += 1
                continue
            start, all_day = _parse_dt(item.get("start"))
            if not start:
                continue
            end, _ = _parse_dt(item.get("end"))
            created = repository.create_calendar_event(
                company_id,
                CalendarEventCreate(
                    title=item.get("summary") or "(bez názvu)",
                    description=item.get("description"),
                    location=item.get("location"),
                    start_at=start, end_at=end, all_day=all_day),
                created_by=None)
            repository.set_google_mapping(company_id, created.id, gid)
            log("import", "ok", backend_event_id=created.id, google_event_id=gid,
                direction="pull")
            stats["pulled"] += 1

    return stats
