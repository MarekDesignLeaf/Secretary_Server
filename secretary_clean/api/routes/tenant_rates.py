"""Tenant default service rates — backed by clean_tenant_service_rates.

Android contract (SettingsScreen rates section):
    GET    /tenant/default-rates/{tenant_id}            -> [ {rate_type, rate, description, is_builtin, ...} ]
    PUT    /tenant/default-rates/{tenant_id}            <- {rate_type: {"rate": number}, ...} -> updated list
    POST   /tenant/service-rate-types/{tenant_id}       <- {rate_type, description, rate}
    DELETE /tenant/service-rate-types/{tenant_id}/{rate_type}

The tenant_id path segment is legacy (Android sends 1) — the real tenant
always comes from the JWT.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository, require_permission
from secretary_clean.core.models import Permission, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/tenant", tags=["tenant rates"])


def _company_currency(repository, company_id: str) -> str:
    company = repository.get_company(company_id)
    return getattr(company, "default_currency", None) or "GBP"


@router.get("/default-rates/{tenant_id}")
def get_default_rates(
    tenant_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return repository.list_tenant_service_rates(user.company_id)


@router.put("/default-rates/{tenant_id}")
def update_default_rates(
    tenant_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Body: {rate_type: {"rate": number}} (also accepts {rate_type: number})."""
    amounts: dict[str, float] = {}
    for rate_type, value in (payload or {}).items():
        raw = value.get("rate") if isinstance(value, dict) else value
        try:
            amounts[str(rate_type)] = float(raw)
        except (TypeError, ValueError):
            continue
    rows = repository.set_tenant_service_rate_amounts(user.company_id, amounts)
    repository.log_activity(
        user.company_id, user.id, "tenant", user.company_id, "update_default_rates",
        f"Default rates updated ({len(amounts)} keys)", source_channel="app")
    return rows


@router.post("/service-rate-types/{tenant_id}", status_code=201)
def add_service_rate_type(
    tenant_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    rate_type = str(payload.get("rate_type") or "").strip()
    if not rate_type:
        raise HTTPException(status_code=422, detail="rate_type is required")
    try:
        rate = float(payload.get("rate") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    try:
        row = repository.create_tenant_service_rate(
            user.company_id, rate_type,
            description=str(payload.get("description") or ""),
            rate=rate,
            currency=_company_currency(repository, user.company_id),
            is_builtin=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.log_activity(
        user.company_id, user.id, "tenant", user.company_id, "add_rate_type",
        f"Rate type added: {rate_type}", source_channel="app")
    return row


@router.delete("/service-rate-types/{tenant_id}/{rate_type}")
def delete_service_rate_type(
    tenant_id: str,
    rate_type: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        repository.delete_tenant_service_rate(user.company_id, rate_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.log_activity(
        user.company_id, user.id, "tenant", user.company_id, "delete_rate_type",
        f"Rate type deleted: {rate_type}", source_channel="app")
    return {"ok": True, "rate_type": rate_type}
