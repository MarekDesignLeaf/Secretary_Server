"""Work Reports router — Phase 2 of Secretary clean backend.

Endpoints:
    POST   /work-reports                          — create a new work report
    GET    /work-reports                          — list work reports for tenant
    GET    /work-reports/{work_report_id}         — get single work report
    POST   /crm/invoices/from-work-report         — create invoice from work report
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository, require_permission
from secretary_clean.core.models import (
    CRMRecord,
    InvoiceFromWorkReportRequest,
    Permission,
    UserAccount,
    WorkReportCreate,
)
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(tags=["work-reports"])


@router.post("/work-reports", response_model=CRMRecord, status_code=201)
def create_work_report(
    body: WorkReportCreate,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return repository.create_work_report(user.company_id, body)


@router.get("/work-reports", response_model=list[CRMRecord])
def list_work_reports(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return repository.list_work_reports(user.company_id)


@router.get("/work-reports/{work_report_id}", response_model=CRMRecord)
def get_work_report(
    work_report_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    record = repository.get_work_report(work_report_id, user.company_id)
    if not record:
        raise HTTPException(status_code=404, detail="Work report not found")
    return record


@router.post("/crm/invoices/from-work-report", response_model=CRMRecord, status_code=201)
def create_invoice_from_work_report(
    body: InvoiceFromWorkReportRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.create_invoice_from_work_report(user.company_id, body, user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
