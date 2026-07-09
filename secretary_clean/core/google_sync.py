"""Google Calendar sync — SAFE one-way push (backend → Google).

History: a two-way auto-reconciling version was tried and had to be reverted —
its pull-import was not idempotent and re-imported every Google event as a new
backend event on each run, flooding the calendar (hundreds of duplicates) and
hammering Google (HTTP 429/403 rate limits). This restores the original, safe
behavior:

  * push-create ONLY: a backend event with no Google mapping is created in
    Google once, then remembered (mapping) and skipped forever after.
  * NO unconditional PATCH of already-synced events (that "rewrote" events on
    every cycle).
  * NO pull-import (that caused the duplication flood).
  * NO automatic deletion of Google or backend events (destructive).

Two-way sync can be revisited later, but only with a persistent, idempotent
dedup key and per-account rate limiting.
"""
from __future__ import annotations

import urllib.parse
from datetime import timedelta


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


def reconcile(repository, company_id: str, calendar_id: str, token: str, gapi,
              since=None) -> dict:
    """One-way push: create not-yet-mapped backend events in Google. `since` is
    accepted for signature compatibility but unused. Every failure is logged;
    already-mapped events are skipped (no rewrite)."""
    cal = urllib.parse.quote(calendar_id)
    stats = {"pushed": 0, "updated": 0, "pushed_deleted": 0,
             "pulled": 0, "pulled_deleted": 0, "skipped": 0, "failed": 0}

    backend_events = repository.list_calendar_events(company_id)
    mapped = {m["backend_event_id"] for m in repository.list_google_mappings(company_id)}

    for ev in backend_events:
        if ev.id in mapped:
            stats["skipped"] += 1
            continue
        ok, data, err = gapi("POST", f"/calendars/{cal}/events", token, _event_body(ev))
        if ok and data.get("id"):
            repository.set_google_mapping(company_id, ev.id, data["id"])
            repository.add_google_sync_log(company_id, "push", "create", "ok",
                                           backend_event_id=ev.id,
                                           google_event_id=data["id"])
            stats["pushed"] += 1
        else:
            repository.add_google_sync_log(company_id, "push", "create", "error",
                                           backend_event_id=ev.id, detail=err)
            stats["failed"] += 1
            # a rate-limit error means Google is throttling — stop this run
            if err and ("429" in err or "rateLimitExceeded" in err or "403" in err):
                break

    return stats
