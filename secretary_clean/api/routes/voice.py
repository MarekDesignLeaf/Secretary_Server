"""Voice foundation routes (Phase A5 — real action execution).

/voice/resolve  — classify an utterance into a backend intent (read-only preview)
/voice/execute  — actually perform the backend action (calendar / task / client)

Voice no longer just "opens a screen": when an action is available it executes
a real repository call. Destructive actions (delete) require confirmed=true.
Work reports are delegated to the multi-turn voice session flow (not executed here).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.language import resolve_language_context
from secretary_clean.core.models import (
    CalendarEventCreate,
    CalendarEventUpdate,
    Permission,
    UserAccount,
    VoiceExecuteRequest,
    VoiceExecuteResult,
    VoiceResolveRequest,
    VoiceResolveResult,
)
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core.voice_intents import parse_intent

router = APIRouter(prefix="/voice", tags=["voice foundation"])


def _lang_ctx(repository, user: UserAccount, client_id: str | None):
    profile = repository.get_tenant_operating_profile(user.company_id)
    client_language = repository.get_client_preferred_language_code(user.company_id, client_id)
    return resolve_language_context(profile=profile, user=user, client_language_code=client_language)


@router.post("/resolve", response_model=VoiceResolveResult)
def resolve_voice_command(
    payload: VoiceResolveRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Classify an utterance (read-only). Does not perform any action."""
    parsed = parse_intent(payload.utterance)
    return VoiceResolveResult(
        utterance=payload.utterance,
        resolved_intent=parsed.intent,
        confidence=parsed.confidence,
        requires_confirmation=parsed.requires_confirmation,
        reason=parsed.reason,
        language_context=_lang_ctx(repository, user, payload.client_id),
    )


def _find_event_for_voice(repository, company_id: str, date_iso: str | None, person: str | None):
    """Best-effort match of an existing event for update/delete.

    Person is the strongest signal (e.g. "meeting with John"). For UPDATE the
    spoken date is usually the *target* date, not the existing one, so when a
    person is given we match on person alone. Date is only used as a fallback
    filter when no person is provided."""
    events = repository.list_calendar_events(company_id)
    if person:
        p = person.lower()
        pm = [e for e in events if p in (e.title or "").lower()]
        if pm:
            return pm
        # person mentioned but no title match → fall through to date filter
    if date_iso:
        return [e for e in events if e.start_at.date().isoformat() == date_iso]
    return events if not person else []


@router.post("/execute", response_model=VoiceExecuteResult)
def execute_voice_command(
    payload: VoiceExecuteRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Resolve the utterance and execute a real backend action."""
    lang_ctx = _lang_ctx(repository, user, payload.client_id)
    parsed = parse_intent(payload.utterance)
    intent = parsed.intent
    ent = parsed.entities

    def result(executed, message, action=None, entity_id=None, data=None, needs_confirm=False):
        return VoiceExecuteResult(
            executed=executed,
            resolved_intent=intent,
            requires_confirmation=needs_confirm,
            message=message,
            action=action,
            entity_id=entity_id,
            data=data or {},
            language_context=lang_ctx,
        )

    if not intent:
        return result(False, "No backend intent matched; no action taken.")

    # Destructive / mutating intents require confirmation
    if parsed.requires_confirmation and not payload.confirmed:
        return result(
            False,
            f"Confirmation required before executing '{intent}'.",
            action=intent, data=ent, needs_confirm=True,
        )

    # ── CALENDAR LIST (read-only) ──────────────────────────────────────────
    if intent == "calendar.list":
        start = end = None
        if ent.get("date"):
            d = ent["date"]
            win = ent.get("window")
            if win:
                start = datetime.fromisoformat(f"{d}T{win[0]}:00+00:00")
                end = datetime.fromisoformat(f"{d}T{win[1]}:00+00:00")
            else:
                start = datetime.fromisoformat(f"{d}T00:00:00+00:00")
                end = datetime.fromisoformat(f"{d}T23:59:59+00:00")
        events = repository.list_calendar_events(user.company_id, start=start, end=end)
        if ent.get("next"):
            now = datetime.now(timezone.utc)
            upcoming = [e for e in repository.list_calendar_events(user.company_id) if e.start_at >= now]
            events = upcoming[:1]
        data = {"events": [e.model_dump(mode="json") for e in events], "count": len(events)}
        return result(True, f"Found {len(events)} event(s).", action=intent, data=data)

    # ── CALENDAR CREATE ────────────────────────────────────────────────────
    if intent == "calendar.create":
        if not ent.get("start_at"):
            return result(False, "Could not determine a date/time for the event.", action=intent, data=ent)
        start = datetime.fromisoformat(ent["start_at"].replace("Z", "+00:00"))
        created = repository.create_calendar_event(
            user.company_id,
            CalendarEventCreate(title=ent.get("title") or "Event", start_at=start),
            created_by=user.id,
        )
        repository.add_calendar_sync_log(user.company_id, created.id, "backend", "created_on_backend",
                                         detail="created via voice")
        return result(True, f"Created '{created.title}'.", action=intent,
                      entity_id=created.id, data={"event": created.model_dump(mode="json")})

    # ── CALENDAR UPDATE ────────────────────────────────────────────────────
    if intent == "calendar.update":
        matches = _find_event_for_voice(repository, user.company_id, ent.get("date"), ent.get("person"))
        if not matches:
            return result(False, "No matching event found to update.", action=intent, data=ent)
        if len(matches) > 1:
            return result(False, f"{len(matches)} events match; be more specific.",
                          action=intent, data={"candidates": [e.model_dump(mode="json") for e in matches]})
        target = matches[0]
        upd = CalendarEventUpdate()
        if ent.get("new_start"):
            upd.start_at = datetime.fromisoformat(ent["new_start"].replace("Z", "+00:00"))
        updated = repository.update_calendar_event(target.id, user.company_id, upd)
        repository.add_calendar_sync_log(user.company_id, updated.id, "backend", "updated_backend",
                                         detail="updated via voice")
        return result(True, f"Moved '{updated.title}'.", action=intent,
                      entity_id=updated.id, data={"event": updated.model_dump(mode="json")})

    # ── CALENDAR DELETE ────────────────────────────────────────────────────
    if intent == "calendar.delete":
        matches = _find_event_for_voice(repository, user.company_id, ent.get("date"), ent.get("person"))
        if not matches:
            return result(False, "No matching event found to cancel.", action=intent, data=ent)
        if len(matches) > 1:
            return result(False, f"{len(matches)} events match; be more specific.",
                          action=intent, data={"candidates": [e.model_dump(mode="json") for e in matches]})
        target = matches[0]
        repository.delete_calendar_event(target.id, user.company_id)
        repository.add_calendar_sync_log(user.company_id, target.id, "backend", "deleted_backend",
                                         detail="deleted via voice")
        return result(True, f"Cancelled '{target.title}'.", action=intent, entity_id=target.id)

    # ── TASK CREATE ────────────────────────────────────────────────────────
    if intent == "task.create":
        name = ("Task for " + ent["person"]) if ent.get("person") else (ent.get("raw") or "Task")
        rec = repository.create_crm_record(
            "tasks", user.company_id, name, {"source": "voice", "assignee": ent.get("person")}
        )
        return result(True, f"Created task: {name}.", action=intent,
                      entity_id=rec.id, data={"task": rec.model_dump(mode="json")})

    # ── CLIENT CREATE ──────────────────────────────────────────────────────
    if intent == "client.create":
        name = ent.get("name")
        if not name:
            return result(False, "Could not determine the client name.", action=intent, data=ent)
        rec = repository.create_crm_record(
            "clients", user.company_id, name, {"source": "voice"}
        )
        return result(True, f"Created client: {name}.", action=intent,
                      entity_id=rec.id, data={"client": rec.model_dump(mode="json")})

    # ── WORK REPORT → delegate to voice session ────────────────────────────
    if intent == "work_report.start":
        return result(
            False,
            "Start a work report via the voice session flow: POST /voice/session/start.",
            action=intent, data={"delegate_to": "/voice/session/start"},
        )

    return result(False, f"Intent '{intent}' is recognised but not executable yet.", action=intent)
