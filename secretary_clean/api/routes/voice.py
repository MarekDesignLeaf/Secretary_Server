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
from pydantic import BaseModel

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.language import resolve_language_context
from secretary_clean.core import help_content
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
    # intent-specific free-text slots. For client.create we collect name, then
    # phone, then address in order: the answer fills the first slot still empty.
    ans = text.strip()
    if intent == "client.create":
        if not d.get("name"):
            d["name"] = ans
        elif not d.get("phone"):
            d["phone"] = ans
        elif not d.get("address"):
            d["address"] = ans
    if intent == "task.create" and not d.get("title"):
        d["title"] = ans
    if intent == "job.create" and not d.get("title"):
        d["title"] = ans
    if intent == "whatsapp.send":
        if not d.get("person"):
            d["person"] = ans
        elif not d.get("message"):
            d["message"] = ans
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

    # ── HELP / NÁPOVĚDA (user- and permission-aware) ──────────────────────
    # Only when no pending action is in progress (help must not interrupt a dialog).
    if not payload.pending_action_id:
        _is_h, _rest = help_content.is_help(payload.utterance)
        if _is_h:
            if not _rest:
                return res(True, help_content.spoken_overview(user), status="executed", action="help")
            _sec = help_content.find_section(user, _rest)
            if _sec is not None:
                return res(True, help_content.spoken_section(user, _sec), status="executed", action="help")
            return res(True, help_content.spoken_overview(user), status="executed", action="help")

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
        phone = data.get("phone")
        address = data.get("address")
        # Duplicate check (blueprint S9): if a client with the same phone or name
        # already exists, do NOT create a duplicate — report it and stop.
        dup = repository.find_duplicate_client(user.company_id, name=name, phone=phone)
        if dup is not None:
            dphone = (dup.data or {}).get("phone")
            extra = f" (telefon {dphone})" if dphone else ""
            return res(False,
                       f"Klienta {dup.name}{extra} už v databázi mám, nevytvářím duplicitu.",
                       status="error", action=intent,
                       entity_id=dup.id, data={"duplicate_of": dup.id})
        rec = repository.create_crm_record("clients", user.company_id, name,
                                           {"source": "voice", "phone": phone, "address": address})
        return res(True, f"Vytvořila jsem klienta: {name}, telefon {phone}, adresa {address}.",
                   action=intent, entity_id=rec.id, data={"client": rec.model_dump(mode="json")})

    if intent == "task.create":
        title = data.get("title")
        person = data.get("person")
        rec = repository.create_crm_record("tasks", user.company_id, title,
                                           {"source": "voice", "assignee": person})
        msg = f"Vytvořila jsem úkol pro {person}: {title}." if person else f"Vytvořila jsem úkol: {title}."
        return res(True, msg, action=intent, entity_id=rec.id, data={"task": rec.model_dump(mode="json")})

    if intent == "calendar.list":
        start = end = None
        rng = data.get("range")
        if rng in ("this_week", "next_week"):
            # 7-day window starting Monday of the (this/next) week.
            now = datetime.now(timezone.utc)
            monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            if rng == "next_week":
                monday = monday + timedelta(days=7)
            start = monday; end = monday + timedelta(days=7) - timedelta(seconds=1)
        elif data.get("date"):
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

    if intent == "calendar.sync":
        from secretary_clean.api.routes import google_calendar as _gc
        acc = repository.get_google_account(user.company_id)
        token = _gc._valid_access_token(repository, acc)
        if not token:
            return res(False, "Google kalendář není připojený. Připoj ho v nastavení.",
                       status="error", action=intent)
        if not acc.google_calendar_id:
            return res(False, "Nemáš vybraný kalendář pro synchronizaci. Vyber ho v nastavení.",
                       status="error", action=intent)
        backend_events = repository.list_calendar_events(user.company_id)
        pushed = skipped = failed = 0
        for ev in backend_events:
            if repository.get_google_mapping(user.company_id, ev.id):
                skipped += 1
                continue
            gid = _gc._push_event_to_google(token, acc.google_calendar_id, ev)
            if gid:
                repository.set_google_mapping(user.company_id, ev.id, gid)
                repository.add_google_sync_log(user.company_id, "push", "create", "ok",
                                               backend_event_id=ev.id, google_event_id=gid)
                pushed += 1
            else:
                failed += 1
        acc.last_sync_at = datetime.now(timezone.utc)
        repository.upsert_google_account(acc)
        if pushed == 0 and skipped > 0:
            msg = "Kalendář je už synchronizovaný, nic nového k nahrání."
        elif pushed > 0:
            msg = f"Synchronizováno. Nahrála jsem {pushed} událostí do Google kalendáře."
        else:
            msg = "Nemáš žádné události k synchronizaci."
        return res(True, msg, action=intent,
                   data={"pushed": pushed, "skipped": skipped, "failed": failed})

    if intent in ("calendar.delete", "calendar.update"):
        # Find the target event by person (matched in title) and/or date.
        person = (data.get("person") or "").strip()
        date_iso = data.get("date")
        candidates = repository.list_calendar_events(user.company_id)
        def _matches(ev):
            ok = True
            if person:
                ok = ok and (person.lower() in (ev.title or "").lower())
            if date_iso:
                ok = ok and (ev.start_at.date().isoformat() == date_iso)
            return ok
        matched = [e for e in candidates if _matches(e)]
        if not matched:
            who = f" s {person}" if person else ""
            return res(False, f"Nenašla jsem schůzku{who} k úpravě. Zkus to upřesnit.",
                       status="error", action=intent)
        if len(matched) > 1:
            matched = sorted(matched, key=lambda e: e.start_at)
        target = matched[0]

        if intent == "calendar.delete":
            ok = repository.delete_calendar_event(target.id, user.company_id)
            if ok:
                return res(True, f"Zrušila jsem schůzku: {target.title}.", action=intent,
                           entity_id=target.id)
            return res(False, "Schůzku se nepodařilo zrušit.", status="error", action=intent)

        # calendar.update — move to a new time
        new_start = data.get("new_start")
        if not new_start:
            return res(False, "Na kdy mám schůzku přesunout? Řekni datum a čas.",
                       status="needs_more_info", action=intent,
                       missing=["new_start"], question="Na kdy mám schůzku přesunout?")
        from datetime import datetime as _dt
        try:
            ns = _dt.fromisoformat(new_start)
        except Exception:
            return res(False, "Nerozuměla jsem novému času. Zkus to znovu.",
                       status="error", action=intent)
        upd = CalendarEventUpdate(start_at=ns)
        updated = repository.update_calendar_event(target.id, user.company_id, upd)
        return res(True, f"Přesunula jsem schůzku {updated.title} na {ns.strftime('%d.%m. %H:%M')}.",
                   action=intent, entity_id=updated.id,
                   data={"event": updated.model_dump(mode="json")})

    if intent == "task.list":
        tasks = repository.list_crm_records("tasks", user.company_id)
        open_tasks = [t for t in tasks if (t.status or "open") not in ("done", "completed", "deleted")]
        if not open_tasks:
            return res(True, "Nemáš žádné otevřené úkoly.", action=intent,
                       data={"tasks": [], "count": 0})
        titles = ", ".join(t.name for t in open_tasks[:10])
        return res(True, f"Máš {len(open_tasks)} úkolů: {titles}.", action=intent,
                   data={"tasks": [t.model_dump(mode="json") for t in open_tasks], "count": len(open_tasks)})

    if intent == "task.complete":
        person = (data.get("person") or "").strip()
        raw = (data.get("raw") or "").lower()
        tasks = repository.list_crm_records("tasks", user.company_id)
        open_tasks = [t for t in tasks if (t.status or "open") not in ("done", "completed", "deleted")]
        def _match(t):
            nm = (t.name or "").lower()
            asg = str((t.data or {}).get("assignee") or "").lower()
            if person:
                return person.lower() in nm or person.lower() in asg
            # otherwise match any title word that appears in the utterance
            return any(w in raw for w in nm.split() if len(w) > 3)
        matched = [t for t in open_tasks if _match(t)]
        if not matched:
            return res(False, "Nenašla jsem odpovídající úkol k dokončení. Zkus to upřesnit.",
                       status="error", action=intent)
        target = matched[0]
        from secretary_clean.core.models import CRMUpdateRequest
        updated = repository.update_crm_record("tasks", target.id, user.company_id,
                                               CRMUpdateRequest(status="done"))
        return res(True, f"Označila jsem úkol jako hotový: {updated.name}.", action=intent,
                   entity_id=updated.id, data={"task": updated.model_dump(mode="json")})

    if intent == "job.create":
        title = data.get("title")
        client = data.get("client")
        extra = {"source": "voice", "client_name": client}
        if client:
            cl = client.strip().lower()
            clients = repository.list_crm_records("clients", user.company_id)
            hits = [c for c in clients if cl in (c.name or "").lower()]
            if len(hits) == 1:
                extra["client_id"] = hits[0].id
                extra["client_name"] = hits[0].name
            elif len(hits) > 1:
                names = ", ".join(c.name for c in hits[:5])
                return res(False, f"Našla jsem víc klientů: {names}. Pro kterého má zakázka být?",
                           status="needs_more_info", action=intent,
                           missing=["client"], question="Pro kterého klienta?")
            else:
                return res(False, f"Klienta '{client}' jsem nenašla. Řekni přesné jméno, nebo ho nejdřív založ.",
                           status="needs_more_info", action=intent,
                           missing=["client"], question=f"Klient {client} neexistuje. Jaké je správné jméno?")
        rec = repository.create_crm_record("jobs", user.company_id, title, extra)
        msg = (f"Vytvořila jsem zakázku: {title} pro {extra['client_name']}." if extra.get("client_id")
               else f"Vytvořila jsem zakázku: {title}.")
        return res(True, msg, action=intent, entity_id=rec.id,
                   data={"job": rec.model_dump(mode="json")})

    if intent == "job.list":
        jobs = repository.list_crm_records("jobs", user.company_id)
        active = [j for j in jobs if (j.status or "open") not in ("uzavřeno", "deleted", "zrušeno")]
        if not active:
            return res(True, "Nemáš žádné aktivní zakázky.", action=intent,
                       data={"jobs": [], "count": 0})
        parts = []
        for j in active[:10]:
            st = j.status or "nová"
            parts.append(f"{j.name} ({st})")
        return res(True, f"Máš {len(active)} zakázek: " + ", ".join(parts) + ".", action=intent,
                   data={"jobs": [j.model_dump(mode="json") for j in active], "count": len(active)})

    if intent == "job.change_status":
        new_status = data.get("new_status")
        if not new_status:
            return res(False, "Na jaký stav mám zakázku změnit? Třeba v realizaci, dokončeno, čeká na materiál.",
                       status="needs_more_info", action=intent,
                       missing=["new_status"], question="Na jaký stav mám zakázku změnit?")
        person = (data.get("person") or "").strip()
        raw = (data.get("raw") or "").lower()
        jobs = repository.list_crm_records("jobs", user.company_id)
        active = [j for j in jobs if (j.status or "open") not in ("uzavřeno", "deleted")]
        def _match(j):
            nm = (j.name or "").lower()
            client = str((j.data or {}).get("client_name") or "").lower()
            if person:
                return person.lower() in nm or person.lower() in client
            return any(w in raw for w in nm.split() if len(w) > 3)
        matched = [j for j in active if _match(j)]
        if not matched:
            return res(False, "Nenašla jsem odpovídající zakázku. Zkus to upřesnit.",
                       status="error", action=intent)
        target = matched[0]
        from secretary_clean.core.models import CRMUpdateRequest
        updated = repository.update_crm_record("jobs", target.id, user.company_id,
                                               CRMUpdateRequest(status=new_status))
        return res(True, f"Změnila jsem stav zakázky {updated.name} na {new_status}.", action=intent,
                   entity_id=updated.id, data={"job": updated.model_dump(mode="json")})

    if intent == "comm.log":
        comm_type = data.get("comm_type") or "hovor"
        person = (data.get("person") or "").strip()
        raw = data.get("raw") or ""
        name = f"{comm_type}" + (f" - {person}" if person else "")
        rec = repository.create_crm_record("communications", user.company_id, name,
                                           {"source": "voice", "type": comm_type,
                                            "contact": person or None, "note": raw})
        msg = (f"Zaznamenala jsem {comm_type} s {person}." if person
               else f"Zaznamenala jsem {comm_type}.")
        return res(True, msg, action=intent, entity_id=rec.id,
                   data={"communication": rec.model_dump(mode="json")})

    if intent == "comm.list":
        person = (data.get("person") or "").strip()
        comms = repository.list_crm_records("communications", user.company_id)
        if person:
            comms = [c for c in comms if person.lower() in (c.name or "").lower()
                     or person.lower() in str((c.data or {}).get("contact") or "").lower()]
        comms = [c for c in comms if (c.status or "open") != "deleted"]
        if not comms:
            return res(True, "Žádná komunikace k zobrazení.", action=intent,
                       data={"communications": [], "count": 0})
        parts = [c.name for c in comms[:10]]
        return res(True, f"Mám {len(comms)} záznamů: " + ", ".join(parts) + ".", action=intent,
                   data={"communications": [c.model_dump(mode="json") for c in comms], "count": len(comms)})

    if intent == "whatsapp.send":
        from secretary_clean.core import whatsapp as _wa
        if not _wa.is_configured():
            return res(False, "WhatsApp není na serveru nakonfigurovaný.",
                       status="error", action=intent)
        person = (data.get("person") or "").strip()
        message = (data.get("message") or "").strip()
        if not message:
            return res(False, "Co mám napsat?", status="needs_more_info",
                       action=intent, missing=["message"], question="Co mám napsat?")
        # Resolve the client's phone from CRM by name match.
        phone = None
        client_name = person
        for c in repository.list_crm_records("clients", user.company_id):
            if person and person.lower() in (c.name or "").lower():
                phone = (c.data or {}).get("phone")
                client_name = c.name
                break
        if not phone:
            return res(False, f"Nenašla jsem telefon klienta {person}. Přidej ho do kontaktu.",
                       status="error", action=intent)
        ok, mid, err = _wa.send_text(phone, message)
        if not ok:
            return res(False, f"WhatsApp se nepodařilo odeslat: {err}",
                       status="error", action=intent)
        # Log the outbound message into communications.
        repository.create_crm_record("communications", user.company_id,
                                     f"whatsapp - {client_name}",
                                     {"source": "voice", "type": "whatsapp",
                                      "direction": "out", "contact": client_name,
                                      "phone": phone, "note": message, "wa_message_id": mid})
        return res(True, f"Odeslala jsem WhatsApp {client_name}: {message}", action=intent,
                   data={"wa_message_id": mid})

    return res(False, f"Intent '{intent}' zatim neumim vykonat.", status="error", action=intent)



@router.get("/help")
def get_help(
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
):
    """Structured help filtered by the user's permissions, in their language.
    Single source of truth shared with the spoken 'help' intent."""
    return help_content.help_for_user(user)


@router.get("/command-tree")
def get_command_tree(
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
):
    """Hierarchical command catalogue (module -> branch -> command), filtered by
    permission and localized. Empty branches are kept for future growth."""
    from secretary_clean.core import command_tree
    return command_tree.tree_for_user(user)



class LearnAliasRequest(BaseModel):
    phrase: str
    answer: str


@router.post("/learn-alias")
def learn_alias(payload: LearnAliasRequest,
                user: UserAccount = Depends(require_permission(Permission.crm_manage))):
    """Adaptive alias learning. Given an unknown phrase and the user's answer
    (the command to map it to), decide the target intent and ACTIVE/PENDING
    status. The alias itself is persisted by the client; this endpoint is the
    single source of the mapping/status logic so voice never bypasses workflow."""
    from secretary_clean.core import alias_learning as al
    if al.is_cancel(payload.answer):
        return {"status": "cancelled", "message": "Dobře, nic neukládám."}
    intent = al.resolve_target_intent(payload.answer)
    if not intent or not al.is_known(intent):
        return {"status": "unknown_target",
                "message": "Tomu příkazu nerozumím. Zkus to říct jinak, nebo řekni omyl."}
    state = al.status_for(intent)
    phrase = payload.phrase.strip()
    # Locate where this command lives in the tree (module > branch) so we can
    # tell the user the correct placement.
    from secretary_clean.core import command_tree
    loc = command_tree.locate_intent(intent)
    loc_txt = ""
    if loc:
        loc_txt = f" ({loc['module_title']} > {loc['branch_title']})"
    if state == "ACTIVE":
        msg = f"Frázi „{phrase}“ jsem přiřadila k příkazu {intent}{loc_txt}. Můžeš ji hned použít."
    else:
        msg = (f"Frázi „{phrase}“ jsem přiřadila k příkazu {intent}{loc_txt}. "
               f"Jakmile bude tato funkce dostupná, příkaz začne fungovat.")
    return {"status": "saved", "alias_status": state, "target_intent": intent,
            "phrase": phrase, "location": loc, "message": msg}
