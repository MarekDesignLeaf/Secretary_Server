"""voice2 executors — one small handler per intent, registry-driven dispatch.

Each handler is `fn(ctx, data) -> H` where H carries the spoken message, the
result payload, and an optional VerifySpec the engine uses for read-back
verification. Handlers NEVER check permissions (engine does) and NEVER write
outside the repository. Messages are authored in Czech; the engine localizes.

The v1 executor semantics (messages, dedup guards, matching rules) are kept —
they are the behavioral contract asserted by the existing test-suite.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from secretary_clean.core.models import (
    CalendarEventCreate, CalendarEventUpdate, CRMUpdateRequest,
    InvoiceFromWorkReportRequest, NoteCreateRequest,
)

# ── plumbing ──────────────────────────────────────────────────────────────────


@dataclass
class Ctx:
    user: object
    repository: object
    utterance: str
    client_id: str | None = None
    dry_run: bool = False        # feasibility check only — no writes


def _ready(message, **kw) -> "H":
    """Feasibility confirmed in a dry-run: the action CAN be done, but the
    engine must ask for confirmation before the real write."""
    return H(executed=False, message=message, status="ready", **kw)


@dataclass
class H:
    executed: bool
    message: str
    status: str = "executed"
    entity_id: str | None = None
    data: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    question: str | None = None
    verify_kind: str | None = None          # "crm:<module>" | "calendar"
    verify_expected: dict = field(default_factory=dict)

    @staticmethod
    def error(message, **kw):
        return H(executed=False, message=message, status="error", **kw)

    @staticmethod
    def ask(message, missing, **kw):
        return H(executed=False, message=message, status="needs_more_info",
                 missing=missing, question=message, **kw)


def _strip(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "")
                   if not unicodedata.combining(c))


def _find_client(repository, company_id: str, name: str):
    """Diacritics-insensitive client match; returns (record|None, list_of_hits)."""
    cl = _strip((name or "").strip().lower())
    if not cl:
        return None, []
    clients = [c for c in repository.list_crm_records("clients", company_id)
               if c.status != "deleted"]
    hits = [c for c in clients if cl in _strip((c.name or "").lower())]
    if not hits:  # stem fallback: first 4 chars of each word (declensions)
        stem = cl.split()[0][:4]
        if len(stem) >= 3:
            hits = [c for c in clients
                    if any(w.startswith(stem) for w in _strip((c.name or "").lower()).split())]
    return (hits[0] if len(hits) == 1 else None), hits


# ── calendar ──────────────────────────────────────────────────────────────────

def calendar_create(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    start = datetime.fromisoformat(data["start_at"].replace("Z", "+00:00"))
    person = data.get("person")
    title = data.get("title") or (f"Schůzka s {person}" if person else "Schůzka")
    existing = [e for e in repository.list_calendar_events(user.company_id)
                if e.title == title and e.start_at == start]
    if existing:
        when = start.strftime("%d.%m. %H:%M")
        return H.error(f"Schůzku '{title}' na {when} už máš v kalendáři.",
                       entity_id=existing[0].id,
                       data={"event": existing[0].model_dump(mode="json")})
    created = repository.create_calendar_event(
        user.company_id, CalendarEventCreate(title=title, start_at=start),
        created_by=user.id)
    repository.add_calendar_sync_log(user.company_id, created.id, "backend",
                                     "created_on_backend", detail="created via voice")
    when = start.strftime("%d.%m. %H:%M")
    return H(True, f"Vytvořila jsem schůzku: {title} ({when}).", entity_id=created.id,
             data={"event": created.model_dump(mode="json")},
             verify_kind="calendar", verify_expected={"title": title})


def calendar_list(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    start = end = None
    rng = data.get("range")
    if rng in ("this_week", "next_week"):
        now = datetime.now(timezone.utc)
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        if rng == "next_week":
            monday += timedelta(days=7)
        start, end = monday, monday + timedelta(days=7) - timedelta(seconds=1)
    elif data.get("date"):
        d, win = data["date"], data.get("window")
        if win:
            start = datetime.fromisoformat(f"{d}T{win[0]}:00+00:00")
            end = datetime.fromisoformat(f"{d}T{win[1]}:00+00:00")
        else:
            start = datetime.fromisoformat(f"{d}T00:00:00+00:00")
            end = datetime.fromisoformat(f"{d}T23:59:59+00:00")
    events = repository.list_calendar_events(user.company_id, start=start, end=end)
    if data.get("next"):
        now = datetime.now(timezone.utc)
        events = [e for e in repository.list_calendar_events(user.company_id)
                  if e.start_at >= now][:1]
    if not events:
        return H(True, "Na ten čas nemáš v kalendáři nic naplánováno.",
                 data={"events": [], "count": 0})
    titles = ", ".join(e.title for e in events)
    return H(True, f"Máš {len(events)} událostí: {titles}.",
             data={"events": [e.model_dump(mode="json") for e in events],
                   "count": len(events)})


def calendar_sync(ctx: Ctx, data: dict) -> H:
    from secretary_clean.api.routes import google_calendar as _gc
    repository, user = ctx.repository, ctx.user
    acc = repository.get_google_account(user.company_id)
    token = _gc._valid_access_token(repository, acc)
    if not token:
        return H.error("Google kalendář není připojený. Připoj ho v nastavení.")
    if not acc.google_calendar_id:
        return H.error("Nemáš vybraný kalendář pro synchronizaci. Vyber ho v nastavení.")
    pushed = skipped = failed = 0
    for ev in repository.list_calendar_events(user.company_id):
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
    return H(True, msg, data={"pushed": pushed, "skipped": skipped, "failed": failed})


def _calendar_target(ctx: Ctx, data: dict, *, for_delete: bool):
    repository, user = ctx.repository, ctx.user
    person = (data.get("person") or "").strip()
    date_iso = data.get("date")

    def _name_match(title: str) -> bool:
        if not person:
            return True
        tnorm = _strip(title.lower())
        pstem = _strip(person.lower())[:4]
        return not pstem or any(w.startswith(pstem) for w in tnorm.split())

    now = datetime.now(timezone.utc)
    candidates = repository.list_calendar_events(user.company_id)
    if for_delete:
        matched = [e for e in candidates if _name_match(e.title or "")
                   and (not date_iso or e.start_at.date().isoformat() == date_iso)]
    else:
        matched = [e for e in candidates if _name_match(e.title or "")]
        upcoming = [e for e in matched if e.start_at >= now - timedelta(hours=1)]
        matched = upcoming or matched
    return sorted(matched, key=lambda e: e.start_at), person


def calendar_delete(ctx: Ctx, data: dict) -> H:
    matched, person = _calendar_target(ctx, data, for_delete=True)
    if not matched:
        who = f" s {person}" if person else ""
        return H.error(f"Nenašla jsem schůzku{who} k úpravě. Zkus to upřesnit.")
    target = matched[0]
    if ctx.dry_run:
        return _ready(f"Zrušit schůzku {target.title}?", entity_id=target.id)
    ok = ctx.repository.delete_calendar_event(target.id, ctx.user.company_id)
    if ok:
        return H(True, f"Zrušila jsem schůzku: {target.title}.", entity_id=target.id,
                 data={"deleted": True})
    return H.error("Schůzku se nepodařilo zrušit.")


def calendar_update(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    matched, person = _calendar_target(ctx, data, for_delete=False)
    if not matched:
        who = f" s {person}" if person else ""
        return H.error(f"Nenašla jsem schůzku{who} k úpravě. Zkus to upřesnit.")
    target = matched[0]
    date_iso = data.get("date")
    if not date_iso and not data.get("time"):
        return H.ask("Na kdy mám schůzku přesunout? Řekni den, případně i čas.",
                     ["new_start"])
    orig = target.start_at
    new_date = date_iso or orig.date().isoformat()
    new_time = data.get("time") or orig.strftime("%H:%M")
    try:
        ns = datetime.fromisoformat(f"{new_date}T{new_time}:00+00:00")
    except Exception:  # noqa: BLE001
        return H.error("Nerozuměla jsem novému termínu. Zkus to znovu.")
    if ctx.dry_run:
        return _ready(f"Přesunout schůzku {target.title} na "
                      f"{ns.strftime('%d.%m. %H:%M')}?", entity_id=target.id)
    new_end = ns + (target.end_at - orig) if target.end_at else None
    updated = repository.update_calendar_event(
        target.id, user.company_id, CalendarEventUpdate(start_at=ns, end_at=new_end))
    repository.add_calendar_sync_log(user.company_id, updated.id, "backend",
                                     "updated_on_backend", detail="moved via voice")
    return H(True, f"Přesunula jsem schůzku {updated.title} na {ns.strftime('%d.%m. %H:%M')}.",
             entity_id=updated.id, data={"event": updated.model_dump(mode="json")},
             verify_kind="calendar", verify_expected={"start_at": ns.isoformat()})


# ── clients ───────────────────────────────────────────────────────────────────

def client_create(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    name, phone, address = data.get("name"), data.get("phone"), data.get("address")
    dup = repository.find_duplicate_client(user.company_id, name=name, phone=phone)
    if dup is not None:
        dphone = (dup.data or {}).get("phone")
        extra = f" (telefon {dphone})" if dphone else ""
        return H.error(f"Klienta {dup.name}{extra} už v databázi mám, nevytvářím duplicitu.",
                       entity_id=dup.id, data={"duplicate_of": dup.id})
    rec = repository.create_crm_record(
        "clients", user.company_id, name,
        {"source": "voice", "phone": phone, "address": address})
    return H(True, f"Vytvořila jsem klienta: {name}, telefon {phone}, adresa {address}.",
             entity_id=rec.id, data={"client": rec.model_dump(mode="json")},
             verify_kind="crm:clients", verify_expected={"name": name, "phone": phone})


def client_find(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    query = (data.get("query") or "").strip()
    if not query:
        return H.ask("Koho mám najít? Řekni jméno.", ["query"])
    q = _strip(query.lower())
    clients = [c for c in repository.list_crm_records("clients", user.company_id)
               if c.status != "deleted"]
    matched = [c for c in clients if q in _strip((c.name or "").lower())
               or any(qt in _strip((c.name or "").lower()) for qt in q.split())]
    if not matched:
        return H.error(f"Kontakt {query} jsem nenašla.")
    target = matched[0]
    d = target.data or {}
    phone = d.get("phone") or d.get("phone_primary")
    address = d.get("billing_address_line1") or d.get("address")

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
    return H(True, msg, entity_id=target.id,
             data={"client": target.model_dump(mode="json"), "phone": phone,
                   "address": address, "open_tasks": len(open_tasks),
                   "active_jobs": len(active_jobs)})


def client_set_address(ctx: Ctx, data: dict) -> H:
    from secretary_clean.core import address_extract
    repository, user = ctx.repository, ctx.user
    person = (data.get("person") or "").strip().lower()
    if not person:
        m = re.search(r"klient(?:ovi|a)?\s+([^\d,]+)$",
                      (data.get("raw") or ctx.utterance), flags=re.IGNORECASE)
        if m:
            person = m.group(1).strip().lower()
    clients = [c for c in repository.list_crm_records("clients", user.company_id)
               if c.status != "deleted"]
    client = None
    if person:
        client = next((c for c in clients if person in (c.name or "").lower()
                       or (c.name or "").lower() in person), None)
    if client is None:
        return H.ask("Kterému klientovi mám adresu doplnit?", ["person"])
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
        return H.error(f"V žádné zprávě od {client.name} jsem nenašla adresu.")
    repository.update_crm_record(
        "clients", client.id, user.company_id,
        CRMUpdateRequest(data={"billing_address_line1": address, "address": address,
                               "address_source": "message"}))
    repository.log_activity(user.company_id, user.id, "client", client.id,
                            "address_filled", f"Adresa doplněna ze zprávy: {address}",
                            source_channel="voice")
    return H(True, f"Adresu klienta {client.name} jsem nastavila na {address}.",
             entity_id=client.id, data={"address": address},
             verify_kind="crm:clients", verify_expected={"address": address})


def client_note(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    person = (data.get("person") or data.get("client") or "").strip()
    note = (data.get("note") or data.get("title") or "").strip()
    if not person:
        return H.ask("Ke kterému klientovi mám poznámku přidat?", ["person"])
    if not note:
        return H.ask("Co mám do poznámky napsat?", ["note"])
    client, hits = _find_client(repository, user.company_id, person)
    if client is None and len(hits) > 1:
        names = ", ".join(c.name for c in hits[:5])
        return H.ask(f"Našla jsem víc klientů: {names}. Ke kterému?", ["person"])
    if client is None:
        return H.error(f"Klienta {person} jsem nenašla.")
    repository.add_crm_note("clients", client.id, user.company_id,
                            NoteCreateRequest(text=note), author_id=user.id)
    repository.log_activity(user.company_id, user.id, "client", client.id,
                            "note_added", note, source_channel="voice")
    return H(True, f"Přidala jsem poznámku ke klientovi {client.name}: {note}.",
             entity_id=client.id, data={"note": note})


# ── tasks ─────────────────────────────────────────────────────────────────────

def task_create(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    title, person = data.get("title"), data.get("person")
    task_data: dict = {"source": "voice", "assignee": person}
    if data.get("date"):
        task_data["planned_date"] = data["date"]
        task_data["deadline"] = data["date"]
    if data.get("start_at"):
        task_data["planned_start_at"] = data["start_at"]
    if data.get("client_id"):
        task_data["client_id"] = data["client_id"]
    rec = repository.create_crm_record("tasks", user.company_id, title, task_data)
    when = f" na {data['date']}" if data.get("date") else ""
    msg = (f"Vytvořila jsem úkol pro {person}{when}: {title}." if person
           else f"Vytvořila jsem úkol{when}: {title}.")
    return H(True, msg, entity_id=rec.id, data={"task": rec.model_dump(mode="json")},
             verify_kind="crm:tasks", verify_expected={"name": title})


def task_list(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    tasks = repository.list_crm_records("tasks", user.company_id)
    open_tasks = [t for t in tasks
                  if (t.status or "open") not in ("done", "completed", "deleted")]
    if not open_tasks:
        return H(True, "Nemáš žádné otevřené úkoly.", data={"tasks": [], "count": 0})
    titles = ", ".join(t.name for t in open_tasks[:10])
    return H(True, f"Máš {len(open_tasks)} úkolů: {titles}.",
             data={"tasks": [t.model_dump(mode="json") for t in open_tasks],
                   "count": len(open_tasks)})


def _match_record(records, person: str, raw: str, extra_field: str):
    def _m(r):
        nm = (r.name or "").lower()
        ex = str((r.data or {}).get(extra_field) or "").lower()
        if person:
            return person.lower() in nm or person.lower() in ex
        return any(w in raw for w in nm.split() if len(w) > 3)
    return [r for r in records if _m(r)]


def task_complete(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    person = (data.get("person") or "").strip()
    raw = (data.get("raw") or "").lower()
    tasks = repository.list_crm_records("tasks", user.company_id)
    open_tasks = [t for t in tasks
                  if (t.status or "open") not in ("done", "completed", "deleted")]
    matched = _match_record(open_tasks, person, raw, "assignee")
    if not matched:
        return H.error("Nenašla jsem odpovídající úkol k dokončení. Zkus to upřesnit.")
    target = matched[0]
    updated = repository.update_crm_record("tasks", target.id, user.company_id,
                                           CRMUpdateRequest(status="done"))
    return H(True, f"Označila jsem úkol jako hotový: {updated.name}.",
             entity_id=updated.id, data={"task": updated.model_dump(mode="json")},
             verify_kind="crm:tasks", verify_expected={"status": "done"})


def task_assign(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    person = (data.get("person") or "").strip()
    raw = (data.get("raw") or ctx.utterance or "").lower()
    if not person:
        return H.ask("Komu mám úkol přiřadit?", ["person"])
    tasks = repository.list_crm_records("tasks", user.company_id)
    open_tasks = [t for t in tasks
                  if (t.status or "open") not in ("done", "completed", "deleted")]
    hint = (data.get("target_hint") or "").lower()
    matched = [t for t in open_tasks
               if any(w in raw or (hint and w in hint)
                      for w in (t.name or "").lower().split() if len(w) > 3)]
    target = matched[0] if matched else (open_tasks[-1] if open_tasks else None)
    if target is None:
        return H.error("Nenašla jsem úkol k přiřazení.")
    d = dict(target.data or {})
    d["assignee"] = person
    updated = repository.update_crm_record("tasks", target.id, user.company_id,
                                           CRMUpdateRequest(data=d))
    return H(True, f"Přiřadila jsem úkol {updated.name} — {person}.",
             entity_id=updated.id, data={"task": updated.model_dump(mode="json")},
             verify_kind="crm:tasks", verify_expected={"assignee": person})


# ── jobs ──────────────────────────────────────────────────────────────────────

def job_create(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    title, client = data.get("title"), data.get("client")
    extra = {"source": "voice", "client_name": client}
    if data.get("date"):
        extra["start_date_planned"] = data["date"]
    if client:
        rec_hit, hits = _find_client(repository, user.company_id, client)
        if rec_hit is not None:
            extra["client_id"] = rec_hit.id
            extra["client_name"] = rec_hit.name
        elif len(hits) > 1:
            names = ", ".join(c.name for c in hits[:5])
            return H.ask(f"Našla jsem víc klientů: {names}. Pro kterého má zakázka být?",
                         ["client"])
        else:
            return H.ask(f"Klienta '{client}' jsem nenašla. Řekni přesné jméno, "
                         f"nebo ho nejdřív založ.", ["client"],
                         question=f"Klient {client} neexistuje. Jaké je správné jméno?")
    rec = repository.create_crm_record("jobs", user.company_id, title, extra)
    msg = (f"Vytvořila jsem zakázku: {title} pro {extra['client_name']}."
           if extra.get("client_id") else f"Vytvořila jsem zakázku: {title}.")
    return H(True, msg, entity_id=rec.id, data={"job": rec.model_dump(mode="json"),
                                                "client_id": extra.get("client_id")},
             verify_kind="crm:jobs",
             verify_expected={"name": title, "client_id": extra.get("client_id")})


def job_list(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    jobs = repository.list_crm_records("jobs", user.company_id)
    active = [j for j in jobs
              if (j.status or "open") not in ("uzavřeno", "deleted", "zrušeno")]
    if not active:
        return H(True, "Nemáš žádné aktivní zakázky.", data={"jobs": [], "count": 0})
    parts = [f"{j.name} ({j.status or 'nová'})" for j in active[:10]]
    return H(True, f"Máš {len(active)} zakázek: " + ", ".join(parts) + ".",
             data={"jobs": [j.model_dump(mode="json") for j in active],
                   "count": len(active)})


def job_change_status(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    new_status = data.get("new_status")
    if not new_status:
        return H.ask("Na jaký stav mám zakázku změnit? Třeba v realizaci, dokončeno, "
                     "čeká na materiál.", ["new_status"],
                     question="Na jaký stav mám zakázku změnit?")
    person = (data.get("person") or "").strip()
    raw = (data.get("raw") or "").lower()
    ref = data.get("entity_ref") or {}
    jobs = repository.list_crm_records("jobs", user.company_id)
    active = [j for j in jobs if (j.status or "open") not in ("uzavřeno", "deleted")]
    if ref.get("kind") == "job":                    # anaphora: "tu zakázku"
        matched = [j for j in active if j.id == ref.get("id")]
    else:
        matched = _match_record(active, person, raw, "client_name")
        if not matched and data.get("target_hint"):
            hint = data["target_hint"].lower()
            matched = [j for j in active
                       if any(w in hint for w in (j.name or "").lower().split()
                              if len(w) > 3)]
    if not matched:
        return H.error("Nenašla jsem odpovídající zakázku. Zkus to upřesnit.")
    target = matched[0]
    updated = repository.update_crm_record("jobs", target.id, user.company_id,
                                           CRMUpdateRequest(status=new_status))
    return H(True, f"Změnila jsem stav zakázky {updated.name} na {new_status}.",
             entity_id=updated.id, data={"job": updated.model_dump(mode="json")},
             verify_kind="crm:jobs", verify_expected={"status": new_status})


# ── work report / billing ────────────────────────────────────────────────────

def work_report_start(ctx: Ctx, data: dict) -> H:
    return H(True, "Spouštím pracovní výkaz. Pověz mi, co se dělalo.",
             status="client_action", data={"client_action": "start_work_report"})


def invoice_from_work_report(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    wr_id = data.get("work_report_id")
    if not wr_id:
        reports = [r for r in repository.list_work_reports(user.company_id)
                   if not (r.data or {}).get("invoiced")]
        client = (data.get("client") or data.get("person") or "").strip().lower()
        if client:
            reports = [r for r in reports
                       if client in str((r.data or {}).get("client_name") or "").lower()
                       or client in (r.name or "").lower()]
        if not reports:
            return H.error("Nenašla jsem žádný nevyfakturovaný pracovní výkaz.")
        reports.sort(key=lambda r: r.created_at, reverse=True)
        wr_id = reports[0].id
    if ctx.dry_run:
        return _ready("Vystavit fakturu z pracovního výkazu?",
                      data={"work_report_id": wr_id})
    try:
        inv = repository.create_invoice_from_work_report(
            user.company_id, InvoiceFromWorkReportRequest(work_report_id=wr_id),
            user_id=user.id)
    except ValueError as exc:
        return H.error(f"Fakturu nejde vystavit: {exc}.")
    except KeyError:
        return H.error("Pracovní výkaz jsem nenašla.")
    total = (inv.data or {}).get("total") or (inv.data or {}).get("total_amount")
    tot = f", částka {total}" if total is not None else ""
    repository.log_activity(user.company_id, user.id, "invoice", inv.id,
                            "created_from_work_report", f"via voice, wr={wr_id}",
                            source_channel="voice")
    return H(True, f"Vystavila jsem fakturu {inv.name}{tot} z pracovního výkazu.",
             entity_id=inv.id, data={"invoice": inv.model_dump(mode="json")},
             verify_kind="crm:invoices", verify_expected={})


def invoice_list(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    invoices = [i for i in repository.list_crm_records("invoices", user.company_id)
                if (i.status or "open") != "deleted"]
    if not invoices:
        return H(True, "Nemáš žádné faktury.", data={"invoices": [], "count": 0})
    unpaid = [i for i in invoices if (i.status or "") not in ("paid", "zaplaceno")]
    parts = [f"{i.name} ({i.status or 'vystavená'})" for i in invoices[:10]]
    return H(True, f"Máš {len(invoices)} faktur, z toho {len(unpaid)} nezaplacených: "
                   + ", ".join(parts) + ".",
             data={"invoices": [i.model_dump(mode="json") for i in invoices],
                   "count": len(invoices), "unpaid": len(unpaid)})


def quote_create(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    client = (data.get("client") or data.get("person") or "").strip()
    title = data.get("title") or (f"Nabídka — {client}" if client else "Nabídka")
    extra = {"source": "voice", "status": "draft"}
    if client:
        rec_hit, hits = _find_client(repository, user.company_id, client)
        if rec_hit is not None:
            extra["client_id"] = rec_hit.id
            extra["client_name"] = rec_hit.name
        elif len(hits) > 1:
            names = ", ".join(c.name for c in hits[:5])
            return H.ask(f"Našla jsem víc klientů: {names}. Pro kterého je nabídka?",
                         ["client"])
        else:
            extra["client_name"] = client
    rec = repository.create_crm_record("quotes", user.company_id, title, extra)
    who = f" pro {extra.get('client_name')}" if extra.get("client_name") else ""
    return H(True, f"Připravila jsem cenovou nabídku{who}: {title}. "
                   f"Položky doplň v aplikaci.",
             entity_id=rec.id, data={"quote": rec.model_dump(mode="json")},
             verify_kind="crm:quotes", verify_expected={"name": title})


def quote_list(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    quotes = [q for q in repository.list_crm_records("quotes", user.company_id)
              if (q.status or "open") != "deleted"]
    if not quotes:
        return H(True, "Nemáš žádné nabídky.", data={"quotes": [], "count": 0})
    parts = [f"{q.name} ({q.status or 'návrh'})" for q in quotes[:10]]
    return H(True, f"Máš {len(quotes)} nabídek: " + ", ".join(parts) + ".",
             data={"quotes": [q.model_dump(mode="json") for q in quotes],
                   "count": len(quotes)})


def lead_create(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    name = (data.get("name") or data.get("person") or data.get("title") or "").strip()
    if not name:
        return H.ask("Jak se zájemce jmenuje?", ["name"])
    rec = repository.create_crm_record(
        "leads", user.company_id, name,
        {"source": "voice", "phone": data.get("phone"), "note": data.get("note")})
    return H(True, f"Zapsala jsem poptávku: {name}.", entity_id=rec.id,
             data={"lead": rec.model_dump(mode="json")},
             verify_kind="crm:leads", verify_expected={"name": name})


def lead_list(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    leads = [l for l in repository.list_crm_records("leads", user.company_id)
             if (l.status or "open") not in ("deleted", "converted")]
    if not leads:
        return H(True, "Nemáš žádné otevřené poptávky.", data={"leads": [], "count": 0})
    parts = [l.name for l in leads[:10]]
    return H(True, f"Máš {len(leads)} poptávek: " + ", ".join(parts) + ".",
             data={"leads": [l.model_dump(mode="json") for l in leads],
                   "count": len(leads)})


# ── misc (ported) ─────────────────────────────────────────────────────────────

def contacts_import(ctx: Ctx, data: dict) -> H:
    return H(True, "Načítám kontakty z telefonu a importuji je do CRM…",
             status="client_action", data={"client_action": "import_contacts"})


def weather_get(ctx: Ctx, data: dict) -> H:
    from secretary_clean.core import weather as _w
    place = (data.get("place") or "").strip()
    if place:
        geo = _w.geocode_place(place)
        if not geo:
            return H.error(f"Nenašla jsem místo {place}.")
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
        return H.error("Počasí se teď nepodařilo načíst.")
    return H(True, msg, data={"location": loc})


def comm_log(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    comm_type = data.get("comm_type") or "hovor"
    person = (data.get("person") or "").strip()
    raw = data.get("raw") or ""
    name = f"{comm_type}" + (f" - {person}" if person else "")
    rec = repository.create_crm_record(
        "communications", user.company_id, name,
        {"source": "voice", "type": comm_type, "contact": person or None, "note": raw})
    msg = (f"Zaznamenala jsem {comm_type} s {person}." if person
           else f"Zaznamenala jsem {comm_type}.")
    return H(True, msg, entity_id=rec.id,
             data={"communication": rec.model_dump(mode="json")},
             verify_kind="crm:communications", verify_expected={})


def comm_list(ctx: Ctx, data: dict) -> H:
    repository, user = ctx.repository, ctx.user
    person = (data.get("person") or "").strip()
    comms = repository.list_crm_records("communications", user.company_id)
    if person:
        comms = [c for c in comms if person.lower() in (c.name or "").lower()
                 or person.lower() in str((c.data or {}).get("contact") or "").lower()]
    comms = [c for c in comms if (c.status or "open") != "deleted"]
    if not comms:
        return H(True, "Žádná komunikace k zobrazení.",
                 data={"communications": [], "count": 0})
    parts = [c.name for c in comms[:10]]
    return H(True, f"Mám {len(comms)} záznamů: " + ", ".join(parts) + ".",
             data={"communications": [c.model_dump(mode="json") for c in comms],
                   "count": len(comms)})


def whatsapp_read(ctx: Ctx, data: dict) -> H:
    from secretary_clean.core import translation as _tr
    repository, user = ctx.repository, ctx.user
    person = (data.get("person") or "").strip().lower()
    inbox = [c for c in repository.list_crm_records("communications", user.company_id)
             if (c.data or {}).get("type") == "whatsapp"
             and (c.data or {}).get("direction") == "in"
             and not (c.data or {}).get("read")]
    if person:
        inbox = [c for c in inbox
                 if person in ((c.data or {}).get("contact") or "").lower()]
    if not inbox:
        return H(True, "Žádné nové zprávy na WhatsAppu.", data={"unread": 0})
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
        repository.update_crm_record("communications", c.id, user.company_id,
                                     CRMUpdateRequest(data={**d, "read": True}))
    more = len(inbox) - len(spoken_parts)
    suffix = f" A ještě {more} dalších." if more > 0 else ""
    msg = (f"Máš {len(inbox)} " +
           ("novou zprávu. " if len(inbox) == 1 else
            "nové zprávy. " if len(inbox) <= 4 else "nových zpráv. ") +
           " ".join(spoken_parts) + suffix)
    return H(True, msg, data={"unread": len(inbox), "read_now": len(spoken_parts)})


def whatsapp_send(ctx: Ctx, data: dict) -> H:
    from secretary_clean.core import whatsapp as _wa
    from secretary_clean.api.routes.whatsapp import outbound_text_for
    repository, user = ctx.repository, ctx.user
    person = (data.get("person") or "").strip()
    message = (data.get("message") or "").strip()
    if not message:
        return H.ask("Co mám napsat?", ["message"])
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
        return H.error(f"Nenašla jsem telefon klienta {person}. Přidej ho do kontaktu.")
    if ctx.dry_run:
        return _ready(f"Poslat WhatsApp {client_name}: {message}?")
    text_to_send, lang_meta = outbound_text_for(
        repository, user.company_id, client_record, message)
    if not _wa.is_configured():
        return H(False, f"Otevírám WhatsApp se zprávou pro {client_name}.",
                 status="client_fallback",
                 data={"phone": phone, "message": text_to_send, "contact": client_name,
                       "translated": lang_meta.get("translated", False)})
    ok, mid, err = _wa.send_text(phone, text_to_send)
    if not ok:
        return H(False, f"Neodeslalo se přes server ({err}). Otevírám WhatsApp se "
                        f"zprávou pro {client_name}.",
                 status="client_fallback",
                 data={"phone": phone, "message": text_to_send, "contact": client_name,
                       "translated": lang_meta.get("translated", False)})
    repository.create_crm_record(
        "communications", user.company_id, f"whatsapp - {client_name}",
        {"source": "voice", "type": "whatsapp", "direction": "out",
         "contact": client_name, "phone": phone, "note": text_to_send,
         "wa_message_id": mid,
         "target_language": lang_meta.get("customer_language"),
         **({"original_text": message} if lang_meta.get("translated") else {})})
    confirm = (f"Odeslala jsem WhatsApp {client_name}: {message}"
               + (" (přeloženo do jazyka zákazníka)." if lang_meta.get("translated") else ""))
    return H(True, confirm,
             data={"wa_message_id": mid, "translated": lang_meta.get("translated", False)})


# ── dispatch table ────────────────────────────────────────────────────────────

HANDLERS = {
    "calendar.list": calendar_list,
    "calendar.create": calendar_create,
    "calendar.update": calendar_update,
    "calendar.delete": calendar_delete,
    "calendar.sync": calendar_sync,
    "client.create": client_create,
    "client.find": client_find,
    "client.set_address": client_set_address,
    "client.note": client_note,
    "task.create": task_create,
    "task.list": task_list,
    "task.complete": task_complete,
    "task.assign": task_assign,
    "job.create": job_create,
    "job.list": job_list,
    "job.change_status": job_change_status,
    "work_report.start": work_report_start,
    "invoice.from_work_report": invoice_from_work_report,
    "invoice.list": invoice_list,
    "quote.create": quote_create,
    "quote.list": quote_list,
    "lead.create": lead_create,
    "lead.list": lead_list,
    "contacts.import": contacts_import,
    "weather.get": weather_get,
    "comm.log": comm_log,
    "comm.list": comm_list,
    "whatsapp.read": whatsapp_read,
    "whatsapp.send": whatsapp_send,
}
