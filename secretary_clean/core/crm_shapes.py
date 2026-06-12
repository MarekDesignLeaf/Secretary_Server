"""Rich CRM shapes — serialize generic CRMRecord into the typed JSON shapes
the Android client models expect (Client, Job, Task, Communication, Invoice,
Quote, Lead, notes). Backend stays the single source of truth: storage is the
generic CRMRecord (name/status/data), these functions only shape the output.

IDs are UUID strings everywhere (Blueprint section 5)."""
from __future__ import annotations

from typing import Any

from secretary_clean.core.models import CRMRecord


def iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _get(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


_CLIENT_FIELDS = (
    "client_code", "client_type", "title", "first_name", "last_name",
    "company_name", "company_registration_no", "vat_no",
    "phone_primary", "phone_secondary", "email_primary", "email_secondary",
    "website", "preferred_contact_method",
    "billing_address_line1", "billing_city", "billing_postcode",
    "billing_country", "owner_user_id", "next_action_task_id",
    "hierarchy_status",
)


def client_out(r: CRMRecord) -> dict:
    d = r.data or {}
    out = {
        "id": r.id,
        "display_name": r.name,
        "status": r.status or "active",
        "created_at": iso(r.created_at),
        "updated_at": iso(r.updated_at),
        "is_commercial": bool(d.get("is_commercial", False)),
        "default_hourly_rate": d.get("default_hourly_rate"),
    }
    for f in _CLIENT_FIELDS:
        out[f] = d.get(f)
    # voice.client.create stores loose keys: phone, address
    if out["phone_primary"] is None:
        out["phone_primary"] = d.get("phone")
    if out["billing_address_line1"] is None:
        out["billing_address_line1"] = d.get("address")
    return out


_JOB_FIELDS = (
    "job_number", "client_id", "property_id", "property_address", "quote_id",
    "start_date_planned", "planned_start_at", "planned_end_at",
    "assigned_user_id", "assigned_to", "next_action_task_id",
    "hierarchy_status", "handover_note", "handed_over_by", "handed_over_at",
)


def job_out(r: CRMRecord, client_names: dict[str, str] | None = None) -> dict:
    d = r.data or {}
    cid = d.get("client_id")
    cname = d.get("client_name") or (client_names or {}).get(str(cid) if cid else "")
    out = {
        "id": r.id,
        "job_title": r.name,
        "job_status": r.status if r.status not in (None, "open") else (d.get("job_status") or "nova"),
        "client_name": cname,
        "calendar_sync_enabled": bool(d.get("calendar_sync_enabled", True)),
        "created_at": iso(r.created_at),
        "updated_at": iso(r.updated_at),
    }
    for f in _JOB_FIELDS:
        out[f] = d.get(f)
    return out


def task_out(r: CRMRecord, client_names: dict[str, str] | None = None) -> dict:
    """Task shape is camelCase (Android Task data class has no @SerializedName)."""
    d = r.data or {}
    cid = d.get("clientId") or d.get("client_id")
    cname = (d.get("clientName") or d.get("client_name")
             or (client_names or {}).get(str(cid) if cid else ""))
    status = r.status if r.status not in (None, "open") else (d.get("status") or "novy")
    notes = d.get("notes") or []
    if notes and isinstance(notes[0], dict):
        notes = [n.get("content", "") for n in notes]
    return {
        "id": r.id,
        "title": r.name,
        "status": status,
        "description": d.get("description"),
        "taskType": _get(d, "taskType", "task_type", default="interni_poznamka"),
        "priority": d.get("priority") or "bezna",
        "createdAt": iso(r.created_at),
        "deadline": d.get("deadline"),
        "plannedDate": _get(d, "plannedDate", "planned_date"),
        "plannedStartAt": _get(d, "plannedStartAt", "planned_start_at"),
        "plannedEndAt": _get(d, "plannedEndAt", "planned_end_at"),
        "timeWindowStart": d.get("timeWindowStart"),
        "timeWindowEnd": d.get("timeWindowEnd"),
        "estimatedMinutes": d.get("estimatedMinutes"),
        "actualMinutes": d.get("actualMinutes"),
        "createdBy": _get(d, "createdBy", "created_by"),
        "assignedUserId": _get(d, "assignedUserId", "assigned_user_id"),
        "assignedTo": _get(d, "assignedTo", "assigned_to", "assignee"),
        "planningNote": d.get("planningNote"),
        "reminderForAssigneeOnly": bool(d.get("reminderForAssigneeOnly", True)),
        "delegatedBy": d.get("delegatedBy"),
        "clientId": cid,
        "clientName": cname,
        "jobId": _get(d, "jobId", "job_id"),
        "propertyId": d.get("propertyId"),
        "propertyAddress": d.get("propertyAddress"),
        "isRecurring": bool(d.get("isRecurring", False)),
        "recurrenceRule": d.get("recurrenceRule"),
        "result": d.get("result"),
        "notes": notes,
        "communicationMethod": d.get("communicationMethod"),
        "source": d.get("source"),
        "isBillable": bool(d.get("isBillable", False)),
        "hasCost": bool(d.get("hasCost", False)),
        "waitingForPayment": bool(d.get("waitingForPayment", False)),
        "checklist": d.get("checklist") or [],
        "calendarSyncEnabled": bool(d.get("calendarSyncEnabled", True)),
        "isCompleted": status in ("done", "hotovo", "completed", "dokonceno", "dokončeno"),
    }


def communication_out(r: CRMRecord, client_names: dict[str, str] | None = None) -> dict:
    d = r.data or {}
    cid = d.get("client_id")
    return {
        "id": r.id,
        "client_id": cid,
        "client_name": (d.get("client_name") or d.get("contact")
                        or (client_names or {}).get(str(cid) if cid else "")),
        "job_id": d.get("job_id"),
        "job_title": d.get("job_title"),
        "comm_type": _get(d, "comm_type", "type", default="telefon"),
        "source": d.get("source"),
        "external_message_id": _get(d, "external_message_id", "wa_message_id"),
        "source_phone": d.get("source_phone"),
        "target_phone": _get(d, "target_phone", "phone"),
        "conversation_key": d.get("conversation_key"),
        "subject": d.get("subject"),
        "message_summary": _get(d, "message_summary", "note", default=r.name),
        "sent_at": d.get("sent_at") or iso(r.created_at),
        "direction": _get(d, "direction", default="inbound"),
        "notes": d.get("notes") if isinstance(d.get("notes"), str) else None,
        "created_at": iso(r.created_at),
        "imported_at": d.get("imported_at"),
    }


def invoice_out(r: CRMRecord, client_names: dict[str, str] | None = None) -> dict:
    d = r.data or {}
    cid = d.get("client_id")
    return {
        "id": r.id,
        "invoice_number": d.get("invoice_number") or r.name,
        "client_id": cid,
        "client_name": (d.get("client_name")
                        or (client_names or {}).get(str(cid) if cid else "")),
        # from-work-report invoices store the amount under "total"
        "grand_total": float(d.get("grand_total") or d.get("total") or 0.0),
        "status": r.status or "draft",
        "due_date": d.get("due_date"),
        "created_at": iso(r.created_at),
    }


def quote_out(r: CRMRecord, client_names: dict[str, str] | None = None) -> dict:
    d = r.data or {}
    cid = d.get("client_id")
    return {
        "id": r.id,
        "quote_number": d.get("quote_number"),
        "client_id": cid,
        "client_name": (d.get("client_name")
                        or (client_names or {}).get(str(cid) if cid else "")),
        "quote_title": d.get("quote_title") or r.name,
        "status": r.status or "draft",
        "grand_total": float(d.get("grand_total") or 0.0),
        "items": d.get("items") or [],
        "created_at": iso(r.created_at),
        "updated_at": iso(r.updated_at),
    }


def lead_out(r: CRMRecord) -> dict:
    d = r.data or {}
    return {
        "id": r.id,
        "lead_code": d.get("lead_code"),
        "lead_source": d.get("lead_source"),
        "contact_name": d.get("contact_name") or r.name,
        "contact_email": d.get("contact_email"),
        "contact_phone": d.get("contact_phone"),
        "description": d.get("description"),
        "notes": d.get("lead_notes"),
        "status": r.status or "new",
        "client_id": d.get("client_id"),
        "job_id": d.get("job_id"),
        "received_at": iso(r.created_at),
        "updated_at": iso(r.updated_at),
    }


def note_out(n: dict, parent_id: str | None = None) -> dict:
    """ClientNote/JobNote shape from data['notes'] entries."""
    return {
        "id": n.get("id", ""),
        "job_id": parent_id,
        "note": n.get("content") or n.get("note", ""),
        "note_type": n.get("note_type", "general"),
        "created_by": n.get("author_name") or n.get("author_id"),
        "created_at": n.get("created_at"),
    }


# Which payload key becomes CRMRecord.name per module (first match wins)
NAME_KEYS = {
    "clients": ("display_name", "name"),
    "jobs": ("job_title", "title", "name"),
    "tasks": ("title", "name"),
    "communications": ("message_summary", "subject", "name"),
    "invoices": ("invoice_number", "name"),
    "quotes": ("quote_title", "name"),
    "leads": ("contact_name", "name"),
}

STATUS_KEYS = {
    "clients": "status",
    "jobs": "job_status",
    "tasks": "status",
    "communications": "status",
    "invoices": "status",
    "quotes": "status",
    "leads": "status",
}

DEFAULT_STATUS = {
    "clients": "active",
    "jobs": "nova",
    "tasks": "novy",
    "communications": "logged",
    "invoices": "draft",
    "quotes": "draft",
    "leads": "new",
}


def split_payload(module: str, payload: dict) -> tuple[str, str, dict]:
    """Extract (name, status, data) from a rich Android payload."""
    data = {k: v for k, v in (payload or {}).items()}
    name = ""
    for key in NAME_KEYS.get(module, ("name",)):
        if data.get(key):
            name = str(data.pop(key))
            break
    status_key = STATUS_KEYS.get(module, "status")
    status = data.pop(status_key, None) or DEFAULT_STATUS.get(module, "open")
    data.pop("id", None)
    return name, str(status), data
