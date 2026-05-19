"""Compatibility routes for Android's legacy activity-pricing API calls.

Android's SecretaryApi.kt was written against the old main.py which used numeric
Long IDs for activities/groups/subtypes and different endpoint paths.  The clean
backend uses string activity_code throughout.  These routes bridge the gap:

  GET  /api/v1/activities/groups                           → catalogue industries as groups
  GET  /api/v1/activities/subtypes/{group_id}              → subtypes for a group
  GET  /api/v1/activities/templates                        → activities as templates (numeric ids)
  GET  /api/v1/activities/tenant/{tenant_id}               → tenant pricing overrides
  PUT  /api/v1/activities/tenant/{tenant_id}/{template_id} → save override
  DELETE /api/v1/activities/tenant/{tenant_id}/{template_id} → reset override

Numeric IDs are derived from CRC32(activity_code) so they are stable across
requests without any database table.
"""

from __future__ import annotations

import zlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.models import TenantActivityOverrideRequest, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/activities", tags=["activities-compat"])


# ---------------------------------------------------------------------------
# Stable numeric ID helpers
# ---------------------------------------------------------------------------

def _cid(code: str) -> int:
    """Return a stable positive 31-bit integer for any string code."""
    return zlib.crc32(code.encode("utf-8")) & 0x7FFF_FFFF


def _activity_registry(request: Request) -> dict[int, Any]:
    """Map numeric_id → WorkActivity for every activity in the catalogue."""
    reg: dict[int, Any] = {}
    for ind in request.app.state.catalogue.industries:
        for sub in ind.subtypes:
            for act in sub.activities:
                reg[_cid(act.code)] = act
    return reg


def _industry_registry(request: Request) -> dict[int, Any]:
    """Map numeric_id → Industry for every industry in the catalogue."""
    return {_cid(ind.code): ind for ind in request.app.state.catalogue.industries}


# ---------------------------------------------------------------------------
# Groups (= industries)
# ---------------------------------------------------------------------------

@router.get("/groups")
def get_activity_groups(request: Request):
    """Return catalogue industries in legacy 'group' format with numeric IDs."""
    return [
        {
            "id": _cid(ind.code),
            "code": ind.code,
            "name": ind.name,
        }
        for ind in sorted(request.app.state.catalogue.industries, key=lambda x: x.display_order)
    ]


# ---------------------------------------------------------------------------
# Subtypes
# ---------------------------------------------------------------------------

@router.get("/subtypes/{group_id}")
def get_activity_subtypes(group_id: int, request: Request):
    """Return subtypes for a catalogue industry (identified by numeric group_id)."""
    industry = _industry_registry(request).get(group_id)
    if not industry:
        raise HTTPException(status_code=404, detail="Group not found")
    return [
        {
            "id": _cid(sub.code),
            "code": sub.code,
            "name": sub.name,
            "group_id": group_id,
        }
        for sub in sorted(industry.subtypes, key=lambda x: x.display_order)
    ]


# ---------------------------------------------------------------------------
# Activity templates
# ---------------------------------------------------------------------------

@router.get("/templates")
def get_activity_templates(
    request: Request,
    subtype_code: str | None = None,
    group_code: str | None = None,
):
    """Return catalogue activities in legacy template format with numeric IDs.

    Android's loadActivityTemplates() calls this with subtype_code=xxx.
    """
    results = []
    for ind in request.app.state.catalogue.industries:
        if group_code and ind.code != group_code:
            continue
        for sub in ind.subtypes:
            if subtype_code and sub.code != subtype_code:
                continue
            for act in sub.activities:
                results.append({
                    "id": _cid(act.code),
                    "code": act.code,
                    "name": act.name,
                    "default_pricing_method": act.default_pricing_method_code,
                    "allowed_pricing_methods": act.available_pricing_method_codes,
                    "subtype_code": act.subtype_code,
                    "industry_code": act.industry_code,
                })
    return results


# ---------------------------------------------------------------------------
# Tenant pricing — read
# ---------------------------------------------------------------------------

@router.get("/tenant/{tenant_id}")
def get_tenant_activity_pricing(
    tenant_id: int,
    request: Request,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
    subtype_code: str | None = None,
):
    """Return tenant pricing overrides merged with catalogue, in legacy format.

    Android calls this alongside /activities/templates so it can match by
    template_id == id.  tenant_id path param is ignored — auth user provides
    the company context.
    """
    existing = {p.activity_code: p for p in repository.list_tenant_pricing(user.company_id)}
    results = []
    for ind in request.app.state.catalogue.industries:
        for sub in ind.subtypes:
            if subtype_code and sub.code != subtype_code:
                continue
            for act in sub.activities:
                override = existing.get(act.code)
                results.append({
                    "template_id": _cid(act.code),
                    "activity_code": act.code,
                    "pricing_method": override.selected_pricing_method_code if override else act.default_pricing_method_code,
                    "rate": (override.rate if override.rate is not None else 0.0) if override else 0.0,
                    "custom_name": override.custom_name or "" if override else "",
                    "is_active": override.is_active if override else True,
                    "enabled_additional_charge_codes": override.enabled_additional_charge_codes if override else [],
                    # Legacy fields expected by Android edit dialog:
                    "notes": "",
                    "voice_aliases": [],
                    "supplementary": {},
                })
    return results


# ---------------------------------------------------------------------------
# Tenant pricing — write
# ---------------------------------------------------------------------------

@router.put("/tenant/{tenant_id}/{template_id}")
def upsert_tenant_activity_pricing(
    tenant_id: int,
    template_id: int,
    body: dict,
    request: Request,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Save a tenant pricing override.

    template_id (numeric) is reverse-looked-up to activity_code.
    Body keys sent by Android edit dialog:
      pricing_method, rate, custom_name, notes, is_active,
      supplementary (Map), voice_aliases (List)
    """
    activity = _activity_registry(request).get(template_id)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity template not found")

    # Normalise pricing_method — Android uses "pricing_method", clean model uses
    # "selected_pricing_method_code".
    method = (
        body.get("pricing_method")
        or body.get("selected_pricing_method_code")
        or activity.default_pricing_method_code
    )

    rate_raw = body.get("rate")
    rate: float | None = None
    if rate_raw is not None:
        try:
            v = float(rate_raw)
            rate = v if v > 0 else None
        except (TypeError, ValueError):
            rate = None

    override_req = TenantActivityOverrideRequest(
        selected_pricing_method_code=method,
        rate=rate,
        custom_name=body.get("custom_name") or None,
        enabled_additional_charge_codes=body.get("enabled_additional_charge_codes") or [],
    )
    saved = repository.save_tenant_pricing(user.company_id, activity.code, override_req)
    return {
        "template_id": template_id,
        "activity_code": saved.activity_code,
        "pricing_method": saved.selected_pricing_method_code,
        "rate": saved.rate if saved.rate is not None else 0.0,
        "custom_name": saved.custom_name or "",
        "is_active": saved.is_active,
        "ok": True,
    }


@router.delete("/tenant/{tenant_id}/{template_id}")
def reset_tenant_activity_pricing(
    tenant_id: int,
    template_id: int,
    request: Request,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Reset a tenant pricing override back to system defaults.

    template_id (numeric) is reverse-looked-up to activity_code.
    """
    activity = _activity_registry(request).get(template_id)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity template not found")
    repository.reset_tenant_pricing(user.company_id, activity.code)
    return {"reset": True, "template_id": template_id, "activity_code": activity.code}
