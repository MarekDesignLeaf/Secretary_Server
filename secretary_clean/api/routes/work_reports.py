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
from secretary_clean.core import crm_shapes as shapes
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


@router.post("/crm/invoices/from-work-report", status_code=201)
def create_invoice_from_work_report(
    body: InvoiceFromWorkReportRequest,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Android reads invoice_number/grand_total/profit/profit_margin from this
    response, so the flat CRMRecord is enriched with the computed fields.
    Profit semantics follow commit 440aa04: cost = worker hourly_cost x hours,
    a missing hourly_cost counts as 0."""
    try:
        invoice = repository.create_invoice_from_work_report(user.company_id, body, user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    work_report = repository.get_work_report(body.work_report_id, user.company_id)
    workers = (work_report.data.get("workers") if work_report else None) or []
    grand_total = float(invoice.data.get("total") or 0.0)
    total_cost = round(sum(
        float(w.get("hourly_cost") or 0) * float(w.get("hours") or 0)
        for w in workers
    ), 2)
    profit = round(grand_total - total_cost, 2)

    out = shapes.invoice_out(invoice)
    out.update({
        "total_cost": total_cost,
        "profit": profit,
        "profit_margin": round(profit / grand_total * 100, 1) if grand_total > 0 else 0.0,
        "currency": invoice.data.get("currency", "GBP"),
        "line_items": invoice.data.get("line_items") or [],
        "pricing_warnings": invoice.data.get("pricing_warnings") or [],
        "work_report_id": invoice.data.get("work_report_id"),
    })
    return out
