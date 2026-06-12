"""CRM routes — clients, jobs, tasks, quotes, invoices, communications, leads.

Responses use the rich shapes the Android client expects (core/crm_shapes.py);
storage stays the generic CRMRecord, so the voice layer and repository are
untouched. IDs are UUID strings (Blueprint section 5).

Fáze 2 (budoucí): photos upload, notifications — zatím prázdné kompatibilní
odpovědi, viz konec souboru. Timeline a calendar-feed jsou reálné.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

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


@router.post("/clients/sync-contacts")
def sync_contacts(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Bulk-import device contacts as clients. Body: {contacts:[{name,phone,email}]}.
    Skips duplicates (by phone/email/name); never creates a second copy."""
    contacts = payload.get("contacts") or []
    imported, skipped = 0, 0
    created_ids = []
    for c in contacts:
        name = str(c.get("name") or "").strip()
        phone = str(c.get("phone") or "").strip()
        if not name and not phone:
            continue
        email = str(c.get("email") or "").strip() or None
        if repository.find_duplicate_client(user.company_id, name=name or None,
                                            phone=phone or None, email=email) is not None:
            skipped += 1
            continue
        rec = repository.create_crm_record(
            "clients", user.company_id, name or phone,
            {"source": "import", "phone": phone or None, "email": email})
        created_ids.append(rec.id)
        imported += 1
    if imported:
        repository.log_activity(
            user.company_id, user.id, "client", "", "import_contacts",
            f"Imported {imported} contacts ({skipped} skipped)", source_channel="app")
    return {"imported": imported, "skipped": skipped,
            "total": len(contacts), "created_ids": created_ids}


def _client_rates_payload(record) -> dict:
    d = record.data or {}
    overrides = d.get("service_rate_overrides") or {}
    # Effective rates = stored base rates with client overrides applied.
    return {
        "client_id": record.id,
        "service_rates": {**(d.get("service_rates") or {}), **overrides},
        "service_rate_overrides": overrides,
        "has_individual_service_rates": bool(overrides),
    }


@router.get("/clients/{client_id}/service-rates")
def get_client_service_rates(
    client_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "clients", client_id, user.company_id)
    return _client_rates_payload(record)


@router.put("/clients/{client_id}/service-rates")
def update_client_service_rates(
    client_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Replaces the whole override map (440aa04 semantics): body is a flat
    {rate_key: rate} dict, only numeric values > 0 are kept."""
    _get_or_404(repository, "clients", client_id, user.company_id)
    normalized: dict[str, float] = {}
    for key, value in (payload or {}).items():
        try:
            rate = float(value)
        except (TypeError, ValueError):
            continue
        if rate > 0:
            normalized[str(key)] = rate
    record = repository.update_crm_record(
        "clients", client_id, user.company_id,
        CRMUpdateRequest(data={"service_rate_overrides": normalized}))
    repository.log_activity(
        user.company_id, user.id, "client", client_id, "update_service_rates",
        f"Service rate overrides set ({len(normalized)} keys)", source_channel="app")
    return _client_rates_payload(record)


@router.put("/clients/{client_id}/rate")
def update_client_rate(
    client_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Single default hourly rate (440aa04: stored on the client and mirrored
    into the hourly_rate override; rate 0 clears it)."""
    record = _get_or_404(repository, "clients", client_id, user.company_id)
    rate = float(payload.get("default_hourly_rate") or 0)
    overrides = dict((record.data or {}).get("service_rate_overrides") or {})
    if rate > 0:
        overrides["hourly_rate"] = rate
    else:
        overrides.pop("hourly_rate", None)
    updated = repository.update_crm_record(
        "clients", client_id, user.company_id,
        CRMUpdateRequest(data={"default_hourly_rate": rate if rate > 0 else None,
                               "service_rate_overrides": overrides}))
    repository.log_activity(
        user.company_id, user.id, "client", client_id, "update_rate",
        f"Default rate: {rate}", source_channel="app")
    return {"id": updated.id, "display_name": updated.name,
            "default_hourly_rate": (updated.data or {}).get("default_hourly_rate")}


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


@router.get("/jobs/{job_id}/audit")
def get_job_audit_log(
    job_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "jobs", job_id, user.company_id)
    return (record.data or {}).get("audit_log") or []


@router.post("/jobs/{job_id}/audit", status_code=201)
def add_job_audit_entry(
    job_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "jobs", job_id, user.company_id)
    entry = {
        "id": str(uuid4()),
        "job_id": job_id,
        "action_type": str(payload.get("action_type") or "note"),
        "description": str(payload.get("description") or ""),
        "user_name": user.display_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    audit = list((record.data or {}).get("audit_log") or [])
    audit.append(entry)
    repository.update_crm_record(
        "jobs", job_id, user.company_id, CRMUpdateRequest(data={"audit_log": audit}))
    repository.log_activity(
        user.company_id, user.id, "job", job_id,
        entry["action_type"], entry["description"], source_channel="app")
    return entry


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
    # Incoming message? Try to fill the client's address from its text.
    filled = None
    if str((record.data or {}).get("direction") or "").lower() == "in":
        filled = autofill_client_address_from_comm(repository, user, record)
    out = shapes.communication_out(record, _client_names(repository, user.company_id))
    if filled:
        out["address_filled_for_client_id"] = filled["client_id"]
        out["address_filled"] = filled["address"]
    return out


def autofill_client_address_from_comm(repository, user, comm_record) -> dict | None:
    """If an inbound message contains an address and its client has none yet,
    fill billing_address_line1 (used by navigation). Returns {client_id,address}
    or None. Never overwrites an existing address."""
    from secretary_clean.core import address_extract
    d = comm_record.data or {}
    text = d.get("note") or d.get("message") or ""
    address = address_extract.extract_address(text)
    if not address:
        return None

    clients = [c for c in _records(repository, "clients", user.company_id)]
    client = None
    cid = d.get("client_id")
    if cid:
        client = next((c for c in clients if c.id == str(cid)), None)
    if client is None:
        contact = str(d.get("contact") or "").strip().lower()
        if contact:
            client = next((c for c in clients if contact in (c.name or "").lower()), None)
    if client is None:
        phone = str(d.get("phone") or "")
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits:
            client = next(
                (c for c in clients
                 if "".join(ch for ch in str((c.data or {}).get("phone") or "") if ch.isdigit()) == digits),
                None)
    if client is None:
        return None

    cd = client.data or {}
    if cd.get("billing_address_line1") or cd.get("address"):
        return None  # never overwrite

    repository.update_crm_record(
        "clients", client.id, user.company_id,
        CRMUpdateRequest(data={"billing_address_line1": address, "address": address,
                               "address_source": "message"}))
    repository.log_activity(
        user.company_id, user.id, "client", client.id, "address_filled",
        f"Adresa doplněna ze zprávy: {address}", source_channel="app")
    return {"client_id": client.id, "address": address}


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


def _invoice_items(repository, record, company_id: str) -> list[dict]:
    """line_items with ids; from-work-report items predate ids, so backfill
    them once and persist (delete-by-id needs a stable id)."""
    items = list((record.data or {}).get("line_items") or [])
    changed = False
    for item in items:
        if not item.get("id"):
            item["id"] = str(uuid4())
            changed = True
    if changed:
        repository.update_crm_record(
            "invoices", record.id, company_id, CRMUpdateRequest(data={"line_items": items}))
    return items


def _items_total(items: list[dict]) -> float:
    return round(sum(float(i.get("subtotal") or 0.0) for i in items), 2)


@router.get("/invoices/{invoice_id}/items")
def list_invoice_items(
    invoice_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "invoices", invoice_id, user.company_id)
    return _invoice_items(repository, record, user.company_id)


@router.post("/invoices/{invoice_id}/items", status_code=201)
def add_invoice_item(
    invoice_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "invoices", invoice_id, user.company_id)
    quantity = float(payload.get("quantity") or 1)
    unit_price = float(payload.get("unit_price") or 0)
    item = {
        "id": str(uuid4()),
        "description": str(payload.get("description") or "Item"),
        "quantity": quantity,
        "unit_price": unit_price,
        "subtotal": round(float(payload.get("total") or quantity * unit_price), 2),
    }
    items = _invoice_items(repository, record, user.company_id)
    items.append(item)
    grand_total = _items_total(items)
    repository.update_crm_record(
        "invoices", invoice_id, user.company_id,
        CRMUpdateRequest(data={"line_items": items, "grand_total": grand_total}))
    repository.log_activity(
        user.company_id, user.id, "invoice", invoice_id, "item_added",
        f"Invoice item: {item['description']} ({item['subtotal']})", source_channel="app")
    return {**item, "invoice_grand_total": grand_total}


@router.delete("/invoices/{invoice_id}/items/{item_id}")
def delete_invoice_item(
    invoice_id: str,
    item_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "invoices", invoice_id, user.company_id)
    items = _invoice_items(repository, record, user.company_id)
    remaining = [i for i in items if i.get("id") != item_id]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail="Invoice item not found")
    grand_total = _items_total(remaining)
    repository.update_crm_record(
        "invoices", invoice_id, user.company_id,
        CRMUpdateRequest(data={"line_items": remaining, "grand_total": grand_total}))
    repository.log_activity(
        user.company_id, user.id, "invoice", invoice_id, "item_deleted",
        f"Invoice item removed ({item_id})", source_channel="app")
    return {"status": "deleted", "invoice_grand_total": grand_total}


@router.get("/invoices/{invoice_id}/payments")
def list_invoice_payments(
    invoice_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "invoices", invoice_id, user.company_id)
    return (record.data or {}).get("payments") or []


@router.post("/invoices/{invoice_id}/payments", status_code=201)
def add_invoice_payment(
    invoice_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Payment status semantics ported from commit 440aa04:
    total paid >= grand_total -> 'uhrazena', partial -> 'castecne_uhrazena'."""
    record = _get_or_404(repository, "invoices", invoice_id, user.company_id)
    amount = float(payload.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be > 0")
    payment = {
        "id": str(uuid4()),
        "amount": amount,
        "payment_date": str(payload.get("payment_date")
                            or datetime.now(timezone.utc).date().isoformat()),
        "payment_method": str(payload.get("payment_method") or "bank_transfer"),
        "reference": payload.get("reference"),
        "notes": payload.get("notes"),
        "created_by": user.id,
    }
    payments = list((record.data or {}).get("payments") or [])
    payments.append(payment)
    total_paid = round(sum(float(p.get("amount") or 0) for p in payments), 2)
    d = record.data or {}
    grand_total = float(d.get("grand_total") or d.get("total") or 0.0)
    status = None
    if grand_total > 0 and total_paid >= grand_total:
        status = "uhrazena"
    elif total_paid > 0:
        status = "castecne_uhrazena"
    repository.update_crm_record(
        "invoices", invoice_id, user.company_id,
        CRMUpdateRequest(status=status, data={"payments": payments}))
    repository.log_activity(
        user.company_id, user.id, "invoice", invoice_id, "payment",
        f"Payment {amount:.2f} ({payment['payment_method']})", source_channel="app")
    return {"id": payment["id"], "amount": amount, "total_paid": total_paid}


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


@router.post("/quotes/{quote_id}/items", status_code=201)
def add_quote_item(
    quote_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "quotes", quote_id, user.company_id)
    quantity = float(payload.get("quantity") or 1)
    unit_price = float(payload.get("unit_price") or 0)
    item = {
        "id": str(uuid4()),
        "description": str(payload.get("description") or "Item"),
        "quantity": quantity,
        "unit_price": unit_price,
        "subtotal": round(float(payload.get("total") or quantity * unit_price), 2),
    }
    items = list((record.data or {}).get("items") or [])
    items.append(item)
    grand_total = _items_total(items)
    updated = repository.update_crm_record(
        "quotes", quote_id, user.company_id,
        CRMUpdateRequest(data={"items": items, "grand_total": grand_total}))
    repository.log_activity(
        user.company_id, user.id, "quote", quote_id, "item_added",
        f"Quote item: {item['description']} ({item['subtotal']})", source_channel="app")
    return shapes.quote_out(updated, _client_names(repository, user.company_id))


@router.delete("/quotes/{quote_id}/items/{item_id}")
def delete_quote_item(
    quote_id: str,
    item_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = _get_or_404(repository, "quotes", quote_id, user.company_id)
    items = list((record.data or {}).get("items") or [])
    remaining = [i for i in items if i.get("id") != item_id]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail="Quote item not found")
    grand_total = _items_total(remaining)
    updated = repository.update_crm_record(
        "quotes", quote_id, user.company_id,
        CRMUpdateRequest(data={"items": remaining, "grand_total": grand_total}))
    repository.log_activity(
        user.company_id, user.id, "quote", quote_id, "item_deleted",
        f"Quote item removed ({item_id})", source_channel="app")
    return shapes.quote_out(updated, _client_names(repository, user.company_id))


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


@router.get("/export/csv")
def export_csv(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Clients export, same columns as the 440aa04 original."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=[
        "id", "client_code", "display_name", "email_primary", "phone_primary", "status"])
    writer.writeheader()
    for r in sorted(_records(repository, "clients", user.company_id),
                    key=lambda r: (r.name or "").lower()):
        d = r.data or {}
        writer.writerow({
            "id": r.id,
            "client_code": d.get("client_code") or "",
            "display_name": r.name,
            "email_primary": d.get("email_primary") or d.get("email") or "",
            "phone_primary": d.get("phone_primary") or d.get("phone") or "",
            "status": r.status or "",
        })
    filename = f"export_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=out.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"})


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
