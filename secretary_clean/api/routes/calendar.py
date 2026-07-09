"""Calendar routes (Phase A3) — backend-owned calendar events.

Endpoints:
    GET    /calendar/events             — list events (optional start/end filter)
    POST   /calendar/events             — create an event
    GET    /calendar/events/{event_id}  — get a single event
    PUT    /calendar/events/{event_id}  — update an event
    DELETE /calendar/events/{event_id}  — delete an event

Backend is the source of truth; all access is tenant-isolated by company_id.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from secretary_clean.api.deps import current_user, get_repository, require_permission
from secretary_clean.core.models import (
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarSyncRequest,
    CalendarSyncResult,
    CalendarSyncLogEntry,
    Permission,
    UserAccount,
)
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/events", response_model=list[CalendarEvent])
def list_events(
    start: datetime | None = Query(default=None, description="Filter: events starting at/after this time"),
    end: datetime | None = Query(default=None, description="Filter: events starting at/before this time"),
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """List calendar events for the tenant, optionally filtered by start window."""
    return repository.list_calendar_events(user.company_id, start=start, end=end)


@router.post("/events", response_model=CalendarEvent, status_code=201)
def create_event(
    payload: CalendarEventCreate,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Create a new calendar event."""
    return repository.create_calendar_event(user.company_id, payload, created_by=user.id)


@router.get("/events/{event_id}", response_model=CalendarEvent)
def get_event(
    event_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Get a single calendar event by id."""
    event = repository.get_calendar_event(event_id, user.company_id)
    if not event:
        raise HTTPException(status_code=404, detail="Calendar event not found")
    return event


@router.put("/events/{event_id}", response_model=CalendarEvent)
def update_event(
    event_id: str,
    payload: CalendarEventUpdate,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Update an existing calendar event."""
    try:
        return repository.update_calendar_event(event_id, user.company_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Calendar event not found") from exc


@router.delete("/events/{event_id}")
def delete_event(
    event_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Delete a calendar event."""
    deleted = repository.delete_calendar_event(event_id, user.company_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Calendar event not found")
    return {"deleted": True, "id": event_id}


@router.post("/events/purge-imported")
def purge_imported_events(
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """One-shot cleanup: remove calendar events with no author (created_by NULL)
    — the duplicates the reverted pull-import created — in a single DB statement.
    User-authored events (created_by set) are untouched."""
    removed = repository.purge_imported_calendar_events(user.company_id)
    return {"removed": removed}


@router.post("/sync", response_model=CalendarSyncResult)
def sync_calendar(
    payload: CalendarSyncRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Synchronize device events with the backend.

    Backend is the source of truth. Conflict resolution: newest updated_at wins.
    Device-only events are imported (source=android_import). Backend-only events
    are returned so the device can create them locally. Every sync action is logged."""
    outcomes = repository.sync_calendar_events(user.company_id, payload.events)
    backend_events = repository.list_calendar_events(user.company_id)
    return CalendarSyncResult(outcomes=outcomes, backend_events=backend_events)


@router.get("/sync-log", response_model=list[CalendarSyncLogEntry])
def get_sync_log(
    limit: int = Query(default=100, ge=1, le=500),
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Return the calendar synchronization log for the tenant (newest first)."""
    return repository.list_calendar_sync_log(user.company_id, limit=limit)
