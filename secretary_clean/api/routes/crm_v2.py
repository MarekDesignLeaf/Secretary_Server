"""CRM routes — clients, jobs, tasks, quotes, invoices, communications, leads.

Responses use the rich shapes the Android client expects (core/crm_shapes.py);
storage stays the generic CRMRecord, so the voice layer and repository are
untouched. IDs are UUID strings (Blueprint section 5).

Fáze 2 (budoucí): photos upload, notifications — zatím prázdné kompatibilní
odpovědi, viz konec souboru. Timeline a calendar-feed jsou reálné.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository, require_permission
from secretary_clean.core import crm_shapes as shapes
from secretary_clean.core.models import (
    CRMRecord,
    CRMUpdateRequest,
    NoteCreateRequest,
    Permission,
    UserAccount,
)
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/crm", tags=["crm core"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(repository, module: str, record_id: str, company_id: str) -> CRMRecord:
    try:
        record = repository.get_crm_record(module, record_id, company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if record is None or record.status == "deleted":
        raise HTTPException(status_code=404, detail=f"{module[:-1].capitalize()} not found")
    return record


def _records(repository, module: str, company_id: str) -> list[CRMRecord]:
    return [r for r in repository.list_crm_records(module, company_id)
            if r.status != "deleted"]


def _client_names(repository, company_id: str) -> dict[str, str]:
    return {r.id: r.name for r in _records(repository, "clients", company_id)}


def _create(repository, module: str, user: UserAccount, payload: dict) -> CRMRecord:
    name, status, data = shapes.split_payload(module, payload)
    if not name:
        raise HTTPException(status_code=422, detail="Name/title is required")
    record = repository.create_crm_record(module, user.company_id, name, data)
    if status:
        record = repository.update_crm_record(
            module, record.id, user.company_id, CRMUpdateRequest(status=status))
    repository.log_activity(
        user.company_id, user.id, module[:-1], record.id,
        "create", f"{module[:-1].capitalize()} created: {name}")
    return record


def _update(repository, module: str, record_id: str, user: UserAccount, payload: dict) -> CRMRecord:
    _get_or_404(repository, module, record_id, user.company_id)
    name, status, data = shapes.split_payload(module, payload)
    req = CRMUpdateRequest(name=name or None, status=payload.get(shapes.STATUS_KEYS[module]) and status or None, data=data or None)
    record = repository.update_crm_record(module, record_id, user.company_id, req)
    repository.log_activity(
        user.company_id, user.id, module[:-1], record_id,
        "update", f"{module[:-1].capitalize()} updated: {record.name}")
    return record


def _notes_list(data: dict | None) -> list[dict]:
    """data['notes'] may be a list of dicts (note API) or a scalar string
    sent in a create payload — never crash the detail view on it."""
    raw = (data or {}).get("notes")
    if isinstance(raw, list):
        return [n for n in raw if isinstance(n, dict)]
    if isinstance(raw, str) and raw.strip():
        return [{"id": "", "content": raw, "note_type": "general"}]
    return []


def _note_payload(data: dict) -> NoteCreateRequest:
    content = (data.get("note") or data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="note content required")
    return NoteCreateRequest(content=content, author_name=data.get("author_name"))


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

@router.get("/clients")
def list_clients(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return [shapes.client_out(r) for r in _records(repository, "clients", user.company_id)]


@router.get("/clients/search")
def search_clients(
    q: str = "",
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    needle = q.lower().strip()
    out = []
    for r in _records(repository, "clients", user.company_id):
        hay = " ".join(str(v) for v in [r.name, *(r.data or {}).values()] if v).lower()
        if not needle or needle in hay:
            out.append(shapes.client_out(r))
    return out


@router.post("/clients")
def create_client(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    dup = repository.find_duplicate_client(
        user.company_id,
        name=payload.get("display_name"),
        email=payload.get("email_primary"),
        phone=payload.get("phone_primary"),
    )
    if dup:
        raise HTTPException(status_code=409, detail=f"Duplicate client: {dup.name}")
    record = _create(repository, "clients", user, payload)
    return shapes.client_out(record)


@router.get("/clients/{client_id}")
def get_client_detail(
    client_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "clients", client_id, user.company_id)
    names = {record.id: record.name}
    jobs = [shapes.job_out(j, names)
            for j in _records(repository, "jobs", user.company_id)
            if str((j.data or {}).get("client_id")) == client_id]
    comms = [shapes.communication_out(c, names)
             for c in _records(repository, "communications", user.company_id)
             if str((c.data or {}).get("client_id")) == client_id]
    tasks = [shapes.task_out(t, names)
             for t in _records(repository, "tasks", user.company_id)
             if str((t.data or {}).get("clientId") or (t.data or {}).get("client_id")) == client_id]
    notes = [shapes.note_out(n) for n in _notes_list(record.data)]
    return {
        "client": shapes.client_out(record),
        "properties": (record.data or {}).get("properties") or [],
        "recent_jobs": jobs,
        "communications": comms,
        "tasks": tasks,
        "notes": notes,
        "service_rates": (record.data or {}).get("service_rates") or {},
        "service_rate_overrides": (record.data or {}).get("service_rate_overrides") or {},
        "has_individual_service_rates": bool((record.data or {}).get("service_rate_overrides")),
    }


@router.put("/clients/{client_id}")
def update_client(
    client_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return shapes.client_out(_update(repository, "clients", client_id, user, payload))


@router.delete("/clients/{client_id}")
def delete_client(
    client_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Soft-delete: sets status='deleted'. Record stays in DB for audit."""
    try:
        repository.delete_crm_record("clients", client_id, user.company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository.log_activity(user.company_id, user.id, "client", client_id,
                            "archive", "Client archived")
    return {"ok": True, "id": client_id, "status": "deleted"}


@router.post("/clients/{client_id}/notes")
def add_client_note(
    client_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    note = _note_payload(payload)
    if not note.author_name:
        note.author_name = user.display_name
    try:
        record = repository.add_crm_note("clients", client_id, user.company_id, note, author_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "notes": [shapes.note_out(n) for n in _notes_list(record.data)]}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@router.get("/jobs")
def list_jobs(
    client_id: str | None = None,
    status: str | None = None,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    names = _client_names(repository, user.company_id)
    out = []
    for r in _records(repository, "jobs", user.company_id):
        if client_id and str((r.data or {}).get("client_id")) != client_id:
            continue
        if status and r.status != status:
            continue
        out.append(shapes.job_out(r, names))
    return out


@router.post("/jobs")
def create_job(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    cid = payload.get("client_id")
    if cid is not None:
        _get_or_404(repository, "clients", str(cid), user.company_id)
    record = _create(repository, "jobs", user, payload)
    return shapes.job_out(record, _client_names(repository, user.company_id))


@router.get("/jobs/{job_id}")
def get_job_detail(
    job_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "jobs", job_id, user.company_id)
    names = _client_names(repository, user.company_id)
    tasks = [shapes.task_out(t, names)
             for t in _records(repository, "tasks", user.company_id)
             if str((t.data or {}).get("jobId") or (t.data or {}).get("job_id")) == job_id]
    notes = [shapes.note_out(n, parent_id=job_id)
             for n in _notes_list(record.data)]
    return {
        "job": shapes.job_out(record, names),
        "tasks": tasks,
        "notes": notes,
        "photos": (record.data or {}).get("photos") or [],
        "audit_log": (record.data or {}).get("audit_log") or [],
    }


@router.put("/jobs/{job_id}")
def update_job(
    job_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _update(repository, "jobs", job_id, user, payload)
    return shapes.job_out(record, _client_names(repository, user.company_id))


@router.post("/jobs/{job_id}/notes")
def add_job_note(
    job_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    note = _note_payload(payload)
    if not note.author_name:
        note.author_name = user.display_name
    try:
        record = repository.add_crm_note("jobs", job_id, user.company_id, note, author_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    new = record.data.get("notes", [])[-1]
    return shapes.note_out(new, parent_id=job_id)


# ---------------------------------------------------------------------------
# Tasks (camelCase shape)
# ---------------------------------------------------------------------------

@router.get("/tasks")
def list_tasks(
    client_id: str | None = None,
    job_id: str | None = None,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    names = _client_names(repository, user.company_id)
    out = []
    for r in _records(repository, "tasks", user.company_id):
        d = r.data or {}
        if client_id and str(d.get("clientId") or d.get("client_id")) != client_id:
            continue
        if job_id and str(d.get("jobId") or d.get("job_id")) != job_id:
            continue
        out.append(shapes.task_out(r, names))
    return out


@router.post("/tasks")
def create_task(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _create(repository, "tasks", user, payload)
    return shapes.task_out(record, _client_names(repository, user.company_id))


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "tasks", task_id, user.company_id)
    return shapes.task_out(record, _client_names(repository, user.company_id))


@router.put("/tasks/{task_id}")
def update_task(
    task_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _update(repository, "tasks", task_id, user, payload)
    return shapes.task_out(record, _client_names(repository, user.company_id))


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Soft-delete: sets status='deleted'."""
    try:
        repository.delete_crm_record("tasks", task_id, user.company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository.log_activity(user.company_id, user.id, "task", task_id,
                            "delete", "Task deleted")
    return {"ok": True, "id": task_id, "status": "deleted"}


# ---------------------------------------------------------------------------
# Communications
# ---------------------------------------------------------------------------

@router.get("/communications")
def list_communications(
    client_id: str | None = None,
    job_id: str | None = None,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    names = _client_names(repository, user.company_id)
    out = []
    for r in _records(repository, "communications", user.company_id):
        d = r.data or {}
        if client_id and str(d.get("client_id")) != client_id:
            continue
        if job_id and str(d.get("job_id")) != job_id:
            continue
        out.append(shapes.communication_out(r, names))
    return out


@router.post("/communications")
def create_communication(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _create(repository, "communications", user, payload)
    return shapes.communication_out(record, _client_names(repository, user.company_id))


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

@router.get("/invoices")
def list_invoices(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    names = _client_names(repository, user.company_id)
    return [shapes.invoice_out(r, names)
            for r in _records(repository, "invoices", user.company_id)]


@router.post("/invoices")
def create_invoice(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _create(repository, "invoices", user, payload)
    return shapes.invoice_out(record, _client_names(repository, user.company_id))


@router.put("/invoices/{invoice_id}")
def update_invoice(
    invoice_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _update(repository, "invoices", invoice_id, user, payload)
    return shapes.invoice_out(record, _client_names(repository, user.company_id))


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------

@router.get("/quotes")
def list_quotes(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    names = _client_names(repository, user.company_id)
    return [shapes.quote_out(r, names)
            for r in _records(repository, "quotes", user.company_id)]


@router.post("/quotes")
def create_quote(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _create(repository, "quotes", user, payload)
    return shapes.quote_out(record, _client_names(repository, user.company_id))


@router.get("/quotes/{quote_id}")
def get_quote(
    quote_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "quotes", quote_id, user.company_id)
    return shapes.quote_out(record, _client_names(repository, user.company_id))


@router.put("/quotes/{quote_id}")
def update_quote(
    quote_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _update(repository, "quotes", quote_id, user, payload)
    return shapes.quote_out(record, _client_names(repository, user.company_id))


@router.post("/quotes/{quote_id}/approve")
def approve_quote(
    quote_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "quotes", quote_id, user.company_id)
    updated = repository.update_crm_record(
        "quotes", quote_id, user.company_id, CRMUpdateRequest(status="approved"))
    repository.log_activity(user.company_id, user.id, "quote", quote_id,
                            "approve", f"Quote approved: {record.name}")
    return shapes.quote_out(updated, _client_names(repository, user.company_id))


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

@router.get("/leads")
def list_leads(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return [shapes.lead_out(r) for r in _records(repository, "leads", user.company_id)]


@router.post("/leads")
def create_lead(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return shapes.lead_out(_create(repository, "leads", user, payload))


@router.get("/leads/{lead_id}")
def get_lead(
    lead_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return shapes.lead_out(_get_or_404(repository, "leads", lead_id, user.company_id))


@router.put("/leads/{lead_id}")
def update_lead(
    lead_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return shapes.lead_out(_update(repository, "leads", lead_id, user, payload))


@router.post("/leads/{lead_id}/convert-to-client")
def convert_lead_to_client(
    lead_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    lead = _get_or_404(repository, "leads", lead_id, user.company_id)
    d = lead.data or {}
    client = repository.create_crm_record("clients", user.company_id, lead.name, {
        "email_primary": d.get("contact_email"),
        "phone_primary": d.get("contact_phone"),
        "source": "lead",
        "lead_id": lead.id,
    })
    repository.update_crm_record("leads", lead_id, user.company_id,
                                 CRMUpdateRequest(status="converted",
                                                  data={"client_id": client.id}))
    repository.log_activity(user.company_id, user.id, "lead", lead_id,
                            "convert", f"Lead converted to client: {lead.name}")
    return shapes.client_out(client)


@router.post("/leads/{lead_id}/convert-to-job")
def convert_lead_to_job(
    lead_id: str,
    payload: dict | None = None,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    lead = _get_or_404(repository, "leads", lead_id, user.company_id)
    d = lead.data or {}
    title = (payload or {}).get("job_title") or f"Zakázka — {lead.name}"
    job = repository.create_crm_record("jobs", user.company_id, title, {
        "client_id": d.get("client_id"),
        "client_name": lead.name,
        "source": "lead",
        "lead_id": lead.id,
    })
    repository.update_crm_record("leads", lead_id, user.company_id,
                                 CRMUpdateRequest(status="converted",
                                                  data={"job_id": job.id}))
    repository.log_activity(user.company_id, user.id, "lead", lead_id,
                            "convert", f"Lead converted to job: {title}")
    return shapes.job_out(job, _client_names(repository, user.company_id))


# ---------------------------------------------------------------------------
# Compat — empty-but-valid responses so existing screens degrade gracefully.
# Fáze 2: photos upload/storage, timeline z clean_activity_log, notifications.
# ---------------------------------------------------------------------------

@router.get("/properties")
def list_properties(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    out = []
    for r in _records(repository, "clients", user.company_id):
        for p in (r.data or {}).get("properties") or []:
            out.append(p)
    return out


@router.get("/timeline")
def crm_timeline(
    limit: int = 100,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return repository.list_activity_log(user.company_id, limit=max(1, min(limit, 300)))


@router.get("/photos")
def list_photos(user: UserAccount = Depends(current_user)):
    return []


@router.get("/notifications")
def list_notifications(user: UserAccount = Depends(current_user)):
    return []


@router.get("/calendar-feed")
def calendar_feed(
    days: int = 30,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Backend calendar events mapped to the Android CalendarFeedEntry shape.

    Every CalendarFeedEntry field is emitted explicitly: Gson instantiates
    Kotlin data classes without running default initializers, so a missing
    key would surface as null in a non-null field on the client.
    """
    days = max(1, min(days, 365))
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    events = repository.list_calendar_events(
        user.company_id, start=start, end=start + timedelta(days=days))
    client_names = _client_names(repository, user.company_id)
    return [
        {
            "entry_key": f"calendar_event:{event.id}",
            "entry_type": "calendar_event",
            "source_id": event.id,
            "title": event.title,
            "client_name": client_names.get(event.client_id) if event.client_id else None,
            "job_title": None,
            "assigned_user_id": None,
            "assigned_to": None,
            "is_assigned_to_current": False,
            "display_mode": "shared",
            "planned_start_at": event.start_at.isoformat(),
            "planned_end_at": event.end_at.isoformat() if event.end_at else None,
            "planned_date": event.start_at.date().isoformat(),
            "description": event.description,
            "calendar_sync_enabled": True,
            "reminder_for_assignee_only": True,
            "status": "scheduled",
        }
        for event in events
    ]
