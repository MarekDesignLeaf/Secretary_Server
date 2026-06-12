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
    CRMUpdateRequest,
    PendingVoiceAction,
    Permission,
    UserAccount,
    VoiceExecuteRequest,
    VoiceExecuteResult,
    VoiceResolveRequest,
    VoiceResolveResult,
)
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core import ai_intent
from secretary_clean.core import voice_intents as vi
from secretary_clean.core import voice_slots as vsl

router = APIRouter(prefix="/voice", tags=["voice foundation"])

PENDING_TTL_MIN = 30

# Learned phrase -> intent, per tenant. The AI resolves an unknown phrasing once;
# it is then recognized instantly (and consistently) next time. Best-effort cache
# (rebuilds after a restart as phrases recur).
_LEARNED: dict[str, dict[str, str]] = {}


def _learn_key(text: str) -> str:
    return " ".join(_strip_diacritics(text.lower()).split())


def _recall_learned(company_id: str, text: str) -> str | None:
    return _LEARNED.get(company_id, {}).get(_learn_key(text))


def _learn(company_id: str, text: str, intent: str) -> None:
    _LEARNED.setdefault(company_id, {})[_learn_key(text)] = intent


def _entities_from_text(intent: str, text: str) -> dict:
    """Light entity extraction for a learned intent when the deterministic
    parser doesn't re-derive them (uses the same primitives)."""
    d: dict = {}
    person = vi.extract_person(text)
    if person:
        d["person"] = person
    date_iso = vi.parse_date(text)
    if date_iso:
        d["date"] = date_iso
        t = vi.parse_time(text)
        d["start_at"] = f"{date_iso}T{t}:00Z" if t else f"{date_iso}T00:00:00Z"
    return d
_CANCEL_WORDS = ("zrus", "zrusit", "cancel", "nech to byt", "to staci", "stop", "nechci",
                 "zapomen na to", "uz ne", "konec", "omyl", "neplatny prikaz", "anuluj")


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
    # calendar.create answer to "S kým nebo jaký název?": accept ANY text as the
    # title (unless the answer was the date/time for the 'when' slot, or it named
    # a person, both already handled above).
    if intent == "calendar.create":
        answered_when = bool(date_iso or hhmm)
        if not answered_when and not d.get("title") and not d.get("person") and text.strip():
            d["title"] = text.strip()
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

    # Voice responses are authored in Czech. The team language can be cs/en/pl;
    # for en/pl we translate the response (and follow-up question) so the
    # assistant answers in the user's language. Czech stays native & instant.
    _app_lang = (user.preferred_language_code or "").split("-")[0].lower()
    if _app_lang not in ("cs", "en", "pl"):
        _prof = repository.get_tenant_operating_profile(user.company_id)
        _app_lang = (getattr(_prof, "default_internal_language_code", "") or "cs").split("-")[0].lower()

    def _localize(msg):
        if not msg or _app_lang == "cs":
            return msg
        target = {"en": "English", "pl": "Polish"}.get(_app_lang)
        if not target:
            return msg
        from secretary_clean.core import translation as _tr
        if not _tr.is_configured():
            return msg
        ok, out, _err = _tr.translate_text(msg, target, "Czech")
        return out if ok and out else msg

    def res(executed, message, status="executed", action=None, entity_id=None,
            data=None, needs_confirm=False, missing=None, question=None, pending_id=None):
        return VoiceExecuteResult(
            executed=executed, resolved_intent=action, requires_confirmation=needs_confirm,
            message=_localize(message), action=action, entity_id=entity_id, data=data or {},
            status=status, missing_fields=missing or [], question=_localize(question),
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
        data = dict(parsed.entities) if intent else {}
        if not intent:
            # Deterministic parser failed → learned cache → AI intent detection.
            learned = _recall_learned(user.company_id, payload.utterance)
            if learned:
                intent = learned
                # Re-extract entities for this intent from the utterance.
                reparsed = vi.parse_intent(payload.utterance)
                data = dict(reparsed.entities) if reparsed.intent == intent else \
                    _entities_from_text(intent, payload.utterance)
            else:
                ai = ai_intent.classify(payload.utterance, getattr(lang_ctx, "internal", None))
                if ai:
                    intent = ai["intent"]
                    data = ai["entities"]
                    _learn(user.company_id, payload.utterance, intent)  # learning
            if not intent:
                return res(False, "Nerozuměl jsem příkazu. Můžeš to říct jinak?",
                           status="error")

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
    if intent == "work_report.start":
        # The work report uses the multi-turn voice SESSION flow; the app picks
        # this up (resolved_intent) and starts /voice/session.
        return res(True, "Spouštím pracovní výkaz. Pověz mi, co se dělalo.",
                   status="client_action", action="work_report.start",
                   data={"client_action": "start_work_report"})

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
        task_data: dict = {"source": "voice", "assignee": person}
        # Spoken date ("na úterý", "zítra") becomes the planned date so the
        # task shows in the calendar dot markers and on the Today screen.
        if data.get("date"):
            task_data["planned_date"] = data["date"]
            task_data["deadline"] = data["date"]
        if data.get("start_at"):
            task_data["planned_start_at"] = data["start_at"]
        rec = repository.create_crm_record("tasks", user.company_id, title, task_data)
        when = f" na {data['date']}" if data.get("date") else ""
        msg = (f"Vytvořila jsem úkol pro {person}{when}: {title}." if person
               else f"Vytvořila jsem úkol{when}: {title}.")
        return res(True, msg, action=intent, entity_id=rec.id, data={"task": rec.model_dump(mode="json")})

    if intent == "contacts.import":
        # Device access lives on the phone — tell the app to read contacts and
        # POST them to /crm/clients/sync-contacts. Backend stays source of truth.
        return res(True, "Načítám kontakty z telefonu a importuji je do CRM…",
                   status="client_action", action=intent,
                   data={"client_action": "import_contacts"})

    if intent == "client.find":
        query = (data.get("query") or "").strip()
        if not query:
            return res(False, "Koho mám najít? Řekni jméno.",
                       status="needs_more_info", action=intent,
                       missing=["query"], question="Koho mám najít?")
        q = _strip_diacritics(query.lower())
        clients = [c for c in repository.list_crm_records("clients", user.company_id)
                   if c.status != "deleted"]
        matched = [c for c in clients if q in _strip_diacritics((c.name or "").lower())
                   or any(qt in _strip_diacritics((c.name or "").lower()) for qt in q.split())]
        if not matched:
            return res(False, f"Kontakt {query} jsem nenašla.", status="error", action=intent)
        target = matched[0]
        d = target.data or {}
        phone = d.get("phone") or d.get("phone_primary")
        address = d.get("billing_address_line1") or d.get("address")
        # Bound records: open tasks / active jobs for this client.
        def _for_client(module, statuses_done):
            out = []
            for r in repository.list_crm_records(module, user.company_id):
                rd = r.data or {}
                cid = str(rd.get("client_id") or rd.get("clientId") or "")
                if cid == target.id and (r.status or "open") not in statuses_done:
                    out.append(r)
            return out
        open_tasks = _for_client("tasks", ("done", "completed", "deleted"))
        active_jobs = _for_client("jobs", ("completed", "cancelled", "deleted"))
        parts = [target.name]
        if phone:
            parts.append(f"telefon {phone}")
        if address:
            parts.append(f"adresa {address}")
        if open_tasks:
            parts.append(f"{len(open_tasks)} otevřených úkolů")
        if active_jobs:
            parts.append(f"{len(active_jobs)} aktivních zakázek")
        msg = ", ".join(parts) + ". Můžeš říct: zavolej, napiš whatsapp, naviguj, nebo vytvoř úkol."
        return res(True, msg, action=intent, entity_id=target.id,
                   data={"client": target.model_dump(mode="json"),
                         "phone": phone, "address": address,
                         "open_tasks": len(open_tasks), "active_jobs": len(active_jobs)})

    if intent == "client.set_address":
        from secretary_clean.core import address_extract
        person = (data.get("person") or "").strip().lower()
        if not person:
            # "doplň adresu klientovi Novák" — name after klient(ovi/a).
            import re as _re
            m = _re.search(r"klient(?:ovi|a)?\s+([^\d,]+)$",
                           (data.get("raw") or payload.utterance), flags=_re.IGNORECASE)
            if m:
                person = m.group(1).strip().lower()
        clients = [c for c in repository.list_crm_records("clients", user.company_id)
                   if c.status != "deleted"]
        client = None
        if person:
            client = next((c for c in clients if person in (c.name or "").lower()
                           or (c.name or "").lower() in person), None)
        if client is None:
            return res(False, "Kterému klientovi mám adresu doplnit?",
                       status="needs_more_info", action=intent, missing=["person"],
                       question="Kterému klientovi?")
        # Most recent inbound message from this client/contact.
        comms = [c for c in repository.list_crm_records("communications", user.company_id)
                 if (c.data or {}).get("direction") == "in"
                 and (str((c.data or {}).get("client_id") or "") == client.id
                      or (client.name or "").lower() in str((c.data or {}).get("contact") or "").lower())]
        comms.sort(key=lambda c: c.created_at, reverse=True)
        address = None
        for c in comms:
            address = address_extract.extract_address((c.data or {}).get("note") or "")
            if address:
                break
        if not address:
            return res(False, f"V žádné zprávě od {client.name} jsem nenašla adresu.",
                       status="error", action=intent)
        repository.update_crm_record(
            "clients", client.id, user.company_id,
            CRMUpdateRequest(data={"billing_address_line1": address, "address": address,
                                   "address_source": "message"}))
        repository.log_activity(
            user.company_id, user.id, "client", client.id, "address_filled",
            f"Adresa doplněna ze zprávy: {address}", source_channel="voice")
        return res(True, f"Adresu klienta {client.name} jsem nastavila na {address}.",
                   action=intent, entity_id=client.id, data={"address": address})

    if intent == "weather.get":
        from secretary_clean.core import weather as _w
        place = (data.get("place") or "").strip()
        if place:
            geo = _w.geocode_place(place)
            if not geo:
                return res(False, f"Nenašla jsem místo {place}.", status="error", action=intent)
        else:
            geo = _w.geocode_place(_w.DEFAULT_PLACE) or {
                "name": _w.DEFAULT_PLACE, "latitude": 51.752, "longitude": -1.2577}
        loc = geo["name"]
        try:
            if data.get("hourly"):
                rows = _w.fetch_hourly(geo["latitude"], geo["longitude"], hours=12)
                parts = []
                for h in rows[:6]:
                    hh = h["time"][11:16]
                    pr = f", {h['precip_prob']}% srážky" if h.get("precip_prob") else ""
                    parts.append(f"{hh} {h['temp']}°C {_w.describe_code(h['code'])}{pr}")
                msg = f"Hodinová předpověď, {loc}: " + "; ".join(parts) + "."
            elif data.get("week"):
                import datetime as _dt
                rows = _w.fetch_daily(geo["latitude"], geo["longitude"], days=7)
                names = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
                parts = []
                for d in rows:
                    y, m, dd = (int(x) for x in d["date"].split("-"))
                    wd = names[_dt.date(y, m, dd).weekday()]
                    pr = f", {d['precip_prob']}% déšť" if d.get("precip_prob") else ""
                    parts.append(f"{wd} {d['tmin']} až {d['tmax']}°C {_w.describe_code(d['code'])}{pr}")
                msg = f"Týdenní předpověď, {loc}: " + "; ".join(parts) + "."
            else:
                rows = _w.fetch_daily(geo["latitude"], geo["longitude"], days=7)
                target = data.get("date")
                day = next((d for d in rows if d["date"] == target), rows[0]) if target else rows[0]
                label = target if target else "Dnes"
                pr = f", {day['precip_prob']}% pravděpodobnost srážek" if day.get("precip_prob") else ""
                msg = (f"{label} v {loc}: {_w.describe_code(day['code'])}, "
                       f"{day['tmin']} až {day['tmax']}°C, vítr {day['wind']} km/h{pr}.")
        except Exception:  # noqa: BLE001
            return res(False, "Počasí se teď nepodařilo načíst.", status="error", action=intent)
        return res(True, msg, action=intent, data={"location": loc})

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
        from datetime import datetime as _dt, timedelta as _td

        person = (data.get("person") or "").strip()
        date_iso = data.get("date")
        # Declension/diacritics-tolerant name match: "s Novákem" must find the
        # event titled "Schůzka s Novák". Compare normalized stems (first 4 chars).
        def _name_match(title: str) -> bool:
            if not person:
                return True
            tnorm = _strip_diacritics(title.lower())
            pstem = _strip_diacritics(person.lower())[:4]
            if not pstem:
                return True
            return any(w.startswith(pstem) for w in tnorm.split())

        now = datetime.now(timezone.utc)
        candidates = repository.list_calendar_events(user.company_id)

        if intent == "calendar.delete":
            # For delete, a spoken date refers to the event's CURRENT day.
            matched = [e for e in candidates if _name_match(e.title or "")
                       and (not date_iso or e.start_at.date().isoformat() == date_iso)]
        else:
            # For move, the spoken date is the DESTINATION — never filter by it.
            # Match by person; prefer upcoming events.
            matched = [e for e in candidates if _name_match(e.title or "")]
            upcoming = [e for e in matched if e.start_at >= now - _td(hours=1)]
            matched = upcoming or matched

        if not matched:
            who = f" s {person}" if person else ""
            return res(False, f"Nenašla jsem schůzku{who} k úpravě. Zkus to upřesnit.",
                       status="error", action=intent)
        target = sorted(matched, key=lambda e: e.start_at)[0]

        if intent == "calendar.delete":
            ok = repository.delete_calendar_event(target.id, user.company_id)
            if ok:
                return res(True, f"Zrušila jsem schůzku: {target.title}.", action=intent,
                           entity_id=target.id)
            return res(False, "Schůzku se nepodařilo zrušit.", status="error", action=intent)

        # calendar.update — move to another day (and/or time)
        if not date_iso and not data.get("time"):
            return res(False, "Na kdy mám schůzku přesunout? Řekni den, případně i čas.",
                       status="needs_more_info", action=intent,
                       missing=["new_start"], question="Na kdy mám schůzku přesunout?")
        # Keep the original time when only a day was said; keep the day when only
        # a time was said.
        orig = target.start_at
        new_date = date_iso or orig.date().isoformat()
        new_time = data.get("time") or orig.strftime("%H:%M")
        try:
            ns = _dt.fromisoformat(f"{new_date}T{new_time}:00+00:00")
        except Exception:
            return res(False, "Nerozuměla jsem novému termínu. Zkus to znovu.",
                       status="error", action=intent)
        # Preserve the event's duration.
        new_end = None
        if target.end_at:
            new_end = ns + (target.end_at - orig)
        upd = CalendarEventUpdate(start_at=ns, end_at=new_end)
        updated = repository.update_calendar_event(target.id, user.company_id, upd)
        repository.add_calendar_sync_log(user.company_id, updated.id, "backend",
                                         "updated_on_backend", detail="moved via voice")
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

    if intent == "whatsapp.read":
        from secretary_clean.core import translation as _tr
        person = (data.get("person") or "").strip().lower()
        inbox = [c for c in repository.list_crm_records("communications", user.company_id)
                 if (c.data or {}).get("type") == "whatsapp"
                 and (c.data or {}).get("direction") == "in"
                 and not (c.data or {}).get("read")]
        if person:
            inbox = [c for c in inbox
                     if person in ((c.data or {}).get("contact") or "").lower()]
        if not inbox:
            return res(True, "Žádné nové zprávy na WhatsAppu.", action=intent,
                       data={"unread": 0})
        inbox.sort(key=lambda c: c.created_at, reverse=True)
        profile = repository.get_tenant_operating_profile(user.company_id)
        internal = getattr(profile, "default_internal_language_code", None)
        auto_in = bool(getattr(profile, "auto_translate_customer_to_internal", True))
        spoken_parts = []
        for c in inbox[:3]:
            d = c.data or {}
            text = d.get("note") or ""
            if auto_in and internal and _tr.is_configured():
                ok_t, translated, _err = _tr.translate_text(text, internal)
                if ok_t and translated:
                    text = translated
            spoken_parts.append(f"Od {d.get('contact') or 'neznámého'}: {text}")
            repository.update_crm_record(
                "communications", c.id, user.company_id,
                CRMUpdateRequest(data={**d, "read": True}))
        more = len(inbox) - len(spoken_parts)
        suffix = f" A ještě {more} dalších." if more > 0 else ""
        msg = (f"Máš {len(inbox)} " +
               ("novou zprávu. " if len(inbox) == 1 else
                "nové zprávy. " if len(inbox) <= 4 else "nových zpráv. ") +
               " ".join(spoken_parts) + suffix)
        return res(True, msg, action=intent,
                   data={"unread": len(inbox), "read_now": len(spoken_parts)})

    if intent == "whatsapp.send":
        from secretary_clean.core import whatsapp as _wa
        from secretary_clean.api.routes.whatsapp import outbound_text_for
        person = (data.get("person") or "").strip()
        message = (data.get("message") or "").strip()
        if not message:
            return res(False, "Co mám napsat?", status="needs_more_info",
                       action=intent, missing=["message"], question="Co mám napsat?")
        # Resolve the client's phone from CRM by name match.
        phone = None
        client_name = person
        client_record = None
        for c in repository.list_crm_records("clients", user.company_id):
            if person and person.lower() in (c.name or "").lower():
                phone = (c.data or {}).get("phone")
                client_name = c.name
                client_record = c
                break
        if not phone:
            return res(False, f"Nenašla jsem telefon klienta {person}. Přidej ho do kontaktu.",
                       status="error", action=intent)
        # Internal language -> customer language rule (tenant profile + client preference).
        text_to_send, lang_meta = outbound_text_for(
            repository, user.company_id, client_record, message)
        if not _wa.is_configured():
            # No Meta credentials on the server: hand the (translated) message
            # back to the app, which opens WhatsApp pre-filled for the user.
            return res(False, f"Otevírám WhatsApp se zprávou pro {client_name}.",
                       status="client_fallback", action=intent,
                       data={"phone": phone, "message": text_to_send,
                             "contact": client_name,
                             "translated": lang_meta.get("translated", False)})
        ok, mid, err = _wa.send_text(phone, text_to_send)
        if not ok:
            return res(False, f"Neodeslalo se přes server ({err}). Otevírám WhatsApp se zprávou pro {client_name}.",
                       status="client_fallback", action=intent,
                       data={"phone": phone, "message": text_to_send,
                             "contact": client_name,
                             "translated": lang_meta.get("translated", False)})
        # Log the outbound message into communications.
        repository.create_crm_record("communications", user.company_id,
                                     f"whatsapp - {client_name}",
                                     {"source": "voice", "type": "whatsapp",
                                      "direction": "out", "contact": client_name,
                                      "phone": phone, "note": text_to_send,
                                      "wa_message_id": mid,
                                      "target_language": lang_meta.get("customer_language"),
                                      **({"original_text": message}
                                         if lang_meta.get("translated") else {})})
        confirm = (f"Odeslala jsem WhatsApp {client_name}: {message}"
                   + (" (přeloženo do jazyka zákazníka)." if lang_meta.get("translated") else ""))
        return res(True, confirm, action=intent,
                   data={"wa_message_id": mid, "translated": lang_meta.get("translated", False)})

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
