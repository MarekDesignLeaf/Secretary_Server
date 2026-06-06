"""Voice foundation routes (Phase A5 + A5.2 — real action execution + pending follow-up).

/voice/resolve  — classify an utterance into a backend intent (read-only preview)
/voice/execute  — perform a real backend action; if required info is missing, ask
                  a follow-up question and keep a backend-owned pending action.

Pending actions live in PostgreSQL (clean_pending_voice_actions), so a multi-turn
dialog survives a backend restart. Android is only a client: it echoes back the
pending_action_id and the next utterance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.language import resolve_language_context
from secretary_clean.core.models import (
    CalendarEventCreate,
    CalendarEventUpdate,
    PendingVoiceAction,
    Permission,
    UserAccount,
    VoiceExecuteRequest,
    VoiceExecuteResult,
    VoiceResolveRequest,
    VoiceResolveResult,
)
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core import voice_intents as vi
from secretary_clean.core import voice_slots as vsl

router = APIRouter(prefix="/voice", tags=["voice foundation"])

PENDING_TTL_MIN = 30
_CANCEL_WORDS = ("zrus", "zrusit", "cancel", "nech to byt", "to staci", "stop", "nechci", "zapomen na to", "uz ne", "konec")


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
    parsed = vi.parse_intent(payload.utterance)
    return VoiceResolveResult(
        utterance=payload.utterance,
        resolved_intent=parsed.intent,
        confidence=parsed.confidence,
        requires_confirmation=parsed.requires_confirmation,
        reason=parsed.reason,
        language_context=_lang_ctx(repository, user, payload.client_id),
    )


def _strip_diacritics(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _is_cancel(text: str) -> bool:
    # Normalize diacritics so "zruš" matches "zrus" (root cause: spoken text keeps
    # diacritics but the cancel-word list is ASCII).
    low = _strip_diacritics(" ".join(text.lower().split()))
    return any(w in low for w in _CANCEL_WORDS)


def _merge_answer_into_data(intent: str, text: str, data: dict) -> dict:
    """Extract entities from a follow-up answer and merge them into collected_data."""
    d = dict(data)
    # date/time
    date_iso = vi.parse_date(text)
    hhmm = vi.parse_time(text)
    if date_iso:
        d["date"] = date_iso
    if hhmm:
        d["time"] = hhmm
    if d.get("date"):
        when = d["date"]
        t = d.get("time")
        d["start_at"] = f"{when}T{t}:00Z" if t else f"{when}T00:00:00Z"
    # person
    person = vi.extract_person(text)
    if person:
        d["person"] = person
    # intent-specific free-text slot
    if intent == "client.create" and not d.get("name"):
        d["name"] = text.strip()
    if intent == "task.create" and not d.get("title"):
        d["title"] = text.strip()
    return d


@router.post("/execute", response_model=VoiceExecuteResult)
def execute_voice_command(
    payload: VoiceExecuteRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Resolve the utterance and execute a real backend action, asking follow-up
    questions when required information is missing (Phase A5.2)."""
    lang_ctx = _lang_ctx(repository, user, payload.client_id)

    def res(executed, message, status="executed", action=None, entity_id=None,
            data=None, needs_confirm=False, missing=None, question=None, pending_id=None):
        return VoiceExecuteResult(
            executed=executed, resolved_intent=action, requires_confirmation=needs_confirm,
            message=message, action=action, entity_id=entity_id, data=data or {},
            status=status, missing_fields=missing or [], question=question,
            pending_action_id=pending_id, language_context=lang_ctx,
        )

    # ── Continuation of an existing pending action ─────────────────────────
    pending = None
    if payload.pending_action_id:
        pending = repository.get_pending_action(payload.pending_action_id, user.company_id)
        if pending and pending.status != "needs_more_info":
            pending = None  # already finished/cancelled — treat as fresh

    # Cancel phrases cancel an active pending action
    if pending and _is_cancel(payload.utterance):
        pending.status = "cancelled"
        repository.update_pending_action(pending)
        return res(False, "Dobře, akci jsem zrušila.", status="cancelled",
                   action=pending.intent, pending_id=pending.id)

    if pending:
        intent = pending.intent
        data = _merge_answer_into_data(intent, payload.utterance, pending.collected_data)
    else:
        parsed = vi.parse_intent(payload.utterance)
        intent = parsed.intent
        if not intent:
            return res(False, "Nerozuměl jsem příkazu.", status="error")
        data = dict(parsed.entities)

    # ── Slot check: is required info still missing? ────────────────────────
    missing = vsl.missing_slots(intent, data)
    if missing:
        question = vsl.next_question(intent, missing)
        now = datetime.now(timezone.utc)
        if pending:
            pending.collected_data = data
            pending.missing_fields = missing
            pending.last_question = question
            repository.update_pending_action(pending)
            pid_out = pending.id
        else:
            new_pending = PendingVoiceAction(
                id=str(uuid.uuid4()), company_id=user.company_id, user_id=user.id,
                intent=intent, status="needs_more_info", collected_data=data,
                missing_fields=missing, last_question=question,
                created_at=now, updated_at=now,
                expires_at=now + timedelta(minutes=PENDING_TTL_MIN),
            )
            repository.create_pending_action(new_pending)
            pid_out = new_pending.id
        return res(False, question, status="needs_more_info", action=intent,
                   data=data, missing=missing, question=question, pending_id=pid_out)

    # All required info present → mark pending ready/executed then run the action.
    if pending:
        pending.collected_data = data
        pending.missing_fields = []
        pending.status = "executed"
        repository.update_pending_action(pending)

    # ── EXECUTE (all required info present) ────────────────────────────────
    if intent == "calendar.create":
        start = datetime.fromisoformat(data["start_at"].replace("Z", "+00:00"))
        person = data.get("person")
        title = data.get("title") or (f"Schůzka s {person}" if person else "Schůzka")
        # Dedup: do not silently create a second identical event at the same time.
        existing = [e for e in repository.list_calendar_events(user.company_id)
                    if e.title == title and e.start_at == start]
        if existing:
            when = start.strftime("%d.%m. %H:%M")
            return res(False, f"Schůzku '{title}' na {when} už máš v kalendáři.",
                       status="error", action=intent, entity_id=existing[0].id,
                       data={"event": existing[0].model_dump(mode="json")})
        created = repository.create_calendar_event(
            user.company_id, CalendarEventCreate(title=title, start_at=start), created_by=user.id)
        repository.add_calendar_sync_log(user.company_id, created.id, "backend", "created_on_backend", detail="created via voice")
        when = start.strftime("%d.%m. %H:%M")
        return res(True, f"Vytvořila jsem schůzku: {title} ({when}).", action=intent,
                   entity_id=created.id, data={"event": created.model_dump(mode="json")})

    if intent == "client.create":
        name = data.get("name")
        rec = repository.create_crm_record("clients", user.company_id, name, {"source": "voice"})
        return res(True, f"Vytvořila jsem klienta: {name}.", action=intent,
                   entity_id=rec.id, data={"client": rec.model_dump(mode="json")})

    if intent == "task.create":
        title = data.get("title")
        person = data.get("person")
        rec = repository.create_crm_record("tasks", user.company_id, title,
                                           {"source": "voice", "assignee": person})
        msg = f"Vytvořila jsem úkol pro {person}: {title}." if person else f"Vytvořila jsem úkol: {title}."
        return res(True, msg, action=intent, entity_id=rec.id, data={"task": rec.model_dump(mode="json")})

    if intent == "calendar.list":
        start = end = None
        if data.get("date"):
            d = data["date"]; win = data.get("window")
            if win:
                start = datetime.fromisoformat(f"{d}T{win[0]}:00+00:00"); end = datetime.fromisoformat(f"{d}T{win[1]}:00+00:00")
            else:
                start = datetime.fromisoformat(f"{d}T00:00:00+00:00"); end = datetime.fromisoformat(f"{d}T23:59:59+00:00")
        events = repository.list_calendar_events(user.company_id, start=start, end=end)
        if data.get("next"):
            now = datetime.now(timezone.utc)
            events = [e for e in repository.list_calendar_events(user.company_id) if e.start_at >= now][:1]
        if not events:
            return res(True, "Na ten čas nemáš v kalendáři nic naplánováno.", action=intent,
                       data={"events": [], "count": 0})
        titles = ", ".join(e.title for e in events)
        return res(True, f"Máš {len(events)} událostí: {titles}.", action=intent,
                   data={"events": [e.model_dump(mode="json") for e in events], "count": len(events)})

    return res(False, f"Intent '{intent}' zatim neumim vykonat.", status="error", action=intent)
