"""Admin routes — activity log viewer and hierarchy integrity report.

GET /api/v1/admin/activity-log         -> recent admin-visible activity entries
GET /api/v1/admin/hierarchy-integrity  -> orphan/blocked-relationship report

Requires users.manage permission (owner/admin). Shapes match the Android
AdminActivityLogEntry and HierarchyIntegrityReport models (string UUID ids).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.models import ActivityLogEntry, Permission, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/admin", tags=["admin"])

_OPEN_TASK_STATUSES = {"open", "new", "in_progress", "pending", ""}


@router.get("/activity-log", response_model=list[ActivityLogEntry])
def activity_log(
    limit: int = 200,
    actor_user_id: str | None = None,
    user: UserAccount = Depends(require_permission(Permission.users_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    rows = repository.list_activity_log(
        user.company_id, limit=max(1, min(limit, 500)), actor_user_id=actor_user_id
    )
    return [
        ActivityLogEntry(**{k: r.get(k) for k in (
            "id", "entity_type", "entity_id", "action", "description",
            "source_channel", "created_at", "actor_user_id",
            "actor_display_name", "actor_email", "details",
        )})
        for r in rows
    ]


@router.get("/hierarchy-integrity")
def hierarchy_integrity(
    user: UserAccount = Depends(require_permission(Permission.users_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    company_id = user.company_id
    clients = repository.list_crm_records("clients", company_id)
    jobs = repository.list_crm_records("jobs", company_id)
    tasks = repository.list_crm_records("tasks", company_id)
    users = repository.list_users(company_id)

    client_ids = {c.id for c in clients}
    job_ids = {j.id for j in jobs}
    task_ids = {t.id for t in tasks}
    user_ids = {u.id for u in users}

    def _sid(value) -> str | None:
        """Normalize a data-dict reference to a string id or None."""
        if value is None:
            return None
        s = str(value).strip()
        return s or None

    orphan_clients: list[dict] = []
    next_action_mismatches: list[dict] = []
    for c in clients:
        data = c.data or {}
        issues: list[str] = []
        owner = _sid(data.get("owner_user_id"))
        if owner and owner not in user_ids:
            issues.append("owner_not_found")
        next_task = _sid(data.get("next_action_task_id"))
        entry = {
            "id": c.id,
            "display_name": c.name,
            "owner_user_id": owner,
            "next_action_task_id": next_task,
            "entity_type": "client",
            "issues": issues,
        }
        if issues:
            orphan_clients.append(entry)
        if next_task and next_task not in task_ids:
            next_action_mismatches.append({**entry, "issues": ["next_action_task_not_found"]})

    orphan_jobs: list[dict] = []
    for j in jobs:
        data = j.data or {}
        issues = []
        cid = _sid(data.get("client_id"))
        if not cid:
            issues.append("missing_client")
        elif cid not in client_ids:
            issues.append("client_not_found")
        assigned = _sid(data.get("assigned_user_id"))
        if assigned and assigned not in user_ids:
            issues.append("assignee_not_found")
        if issues:
            orphan_jobs.append({
                "id": j.id,
                "job_title": j.name,
                "display_name": j.name,
                "client_id": cid,
                "assigned_user_id": assigned,
                "entity_type": "job",
                "issues": issues,
            })

    orphan_tasks: list[dict] = []
    for t in tasks:
        data = t.data or {}
        issues = []
        cid = _sid(data.get("client_id"))
        jid = _sid(data.get("job_id"))
        if not cid and not jid:
            issues.append("missing_parent")
        if cid and cid not in client_ids:
            issues.append("client_not_found")
        if jid and jid not in job_ids:
            issues.append("job_not_found")
        assigned = _sid(data.get("assigned_user_id"))
        if assigned and assigned not in user_ids:
            issues.append("assignee_not_found")
        if issues:
            orphan_tasks.append({
                "id": t.id,
                "title": t.name,
                "client_id": cid,
                "job_id": jid,
                "assigned_user_id": assigned,
                "status": t.status,
                "issues": issues,
            })

    blocked: list[dict] = []
    for u in users:
        owns_clients = any(_sid((c.data or {}).get("owner_user_id")) == u.id for c in clients)
        owns_jobs = any(_sid((j.data or {}).get("assigned_user_id")) == u.id for j in jobs)
        has_open_tasks = any(
            _sid((t.data or {}).get("assigned_user_id")) == u.id
            and (t.status or "").lower() in _OPEN_TASK_STATUSES
            for t in tasks
        )
        is_client_next = any(_sid((c.data or {}).get("next_action_user_id")) == u.id for c in clients)
        is_job_next = any(_sid((j.data or {}).get("next_action_user_id")) == u.id for j in jobs)
        if owns_clients or owns_jobs or has_open_tasks or is_client_next or is_job_next:
            blocked.append({
                "id": u.id,
                "display_name": u.display_name,
                "email": u.email,
                "owns_clients": owns_clients,
                "owns_jobs": owns_jobs,
                "has_open_tasks": has_open_tasks,
                "is_client_next_action_assignee": is_client_next,
                "is_job_next_action_assignee": is_job_next,
            })

    return {
        "orphan_clients": orphan_clients,
        "orphan_jobs": orphan_jobs,
        "orphan_tasks": orphan_tasks,
        "blocked_user_deactivations": blocked,
        "next_action_mismatches": next_action_mismatches,
        "summary": {
            "orphan_clients": len(orphan_clients),
            "orphan_jobs": len(orphan_jobs),
            "orphan_tasks": len(orphan_tasks),
            "blocked_user_deactivations": len(blocked),
            "next_action_mismatches": len(next_action_mismatches),
        },
    }
