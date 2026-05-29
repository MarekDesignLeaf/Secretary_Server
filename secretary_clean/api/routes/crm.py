"""CRM routes — clients, jobs, tasks, quotes, invoices, communications, work_reports.

Fáze 1: list + create + detail CRUD (GET/{id}, PUT/{id}, DELETE/{id}, POST/{id}/notes)
pro clients, jobs, tasks.

Fáze 2 (budoucí): leads, quotes detail, invoices/from-work-report, photos, timeline,
calendar-feed, notifications — viz SECRETARY_CONTEXT_AND_RECOVERY_PLAN.md
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository, require_permission
from secretary_clean.core.models import (
    CRMCreateRequest,
    CRMRecord,
    CRMUpdateRequest,
    NoteCreateRequest,
    Permission,
    UserAccount,
)
from secretary_clean.core.repository import InMemorySecretaryRepository


CRM_MODULES = ("clients", "jobs", "tasks", "quotes", "invoices", "communications", "work_reports")

# Modules that support soft-delete via this router
_DELETABLE = {"clients", "jobs", "tasks"}

router = APIRouter(prefix="/crm", tags=["crm core"])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_or_404(repository, module: str, record_id: str, company_id: str) -> CRMRecord:
    try:
        record = repository.get_crm_record(module, record_id, company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"{module[:-1].capitalize()} not found")
    return record


# ---------------------------------------------------------------------------
# Generic list + create (all 7 modules)
# ---------------------------------------------------------------------------

for module_name in CRM_MODULES:
    def make_list(module: str):
        def list_records(
            user: UserAccount = Depends(current_user),
            repository: InMemorySecretaryRepository = Depends(get_repository),
        ):
            try:
                return repository.list_crm_records(module, user.company_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Unknown CRM module") from exc
        return list_records

    def make_create(module: str):
        def create_record(
            payload: CRMCreateRequest,
            user: UserAccount = Depends(require_permission(Permission.crm_manage)),
            repository: InMemorySecretaryRepository = Depends(get_repository),
        ):
            try:
                return repository.create_crm_record(module, user.company_id, payload.name, payload.data)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Unknown CRM module") from exc
        return create_record

    router.add_api_route(
        f"/{module_name}",
        make_list(module_name),
        methods=["GET"],
        response_model=list[CRMRecord],
        name=f"list_{module_name}",
    )
    router.add_api_route(
        f"/{module_name}",
        make_create(module_name),
        methods=["POST"],
        response_model=CRMRecord,
        name=f"create_{module_name}",
    )


# ---------------------------------------------------------------------------
# Clients — detail CRUD
# ---------------------------------------------------------------------------

@router.get("/clients/{client_id}", response_model=CRMRecord, tags=["clients"])
def get_client(
    client_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return _get_or_404(repository, "clients", client_id, user.company_id)


@router.put("/clients/{client_id}", response_model=CRMRecord, tags=["clients"])
def update_client(
    client_id: str,
    payload: CRMUpdateRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.update_crm_record("clients", client_id, user.company_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/clients/{client_id}", tags=["clients"])
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
    return {"ok": True, "id": client_id, "status": "deleted"}


@router.post("/clients/{client_id}/notes", response_model=CRMRecord, tags=["clients"])
def add_client_note(
    client_id: str,
    payload: NoteCreateRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.add_crm_note("clients", client_id, user.company_id, payload, author_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Jobs — detail CRUD
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}", response_model=CRMRecord, tags=["jobs"])
def get_job(
    job_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return _get_or_404(repository, "jobs", job_id, user.company_id)


@router.put("/jobs/{job_id}", response_model=CRMRecord, tags=["jobs"])
def update_job(
    job_id: str,
    payload: CRMUpdateRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.update_crm_record("jobs", job_id, user.company_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/notes", response_model=CRMRecord, tags=["jobs"])
def add_job_note(
    job_id: str,
    payload: NoteCreateRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.add_crm_note("jobs", job_id, user.company_id, payload, author_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Tasks — detail CRUD
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}", response_model=CRMRecord, tags=["tasks"])
def get_task(
    task_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return _get_or_404(repository, "tasks", task_id, user.company_id)


@router.put("/tasks/{task_id}", response_model=CRMRecord, tags=["tasks"])
def update_task(
    task_id: str,
    payload: CRMUpdateRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.update_crm_record("tasks", task_id, user.company_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/tasks/{task_id}", tags=["tasks"])
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
    return {"ok": True, "id": task_id, "status": "deleted"}
