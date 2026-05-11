from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.models import TenantActivityOverrideRequest, TenantActivityPricing, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/tenant-pricing", tags=["tenant pricing"])


def _activity_by_code(request: Request, activity_code: str):
    for industry in request.app.state.catalogue.industries:
        for subtype in industry.subtypes:
            for activity in subtype.activities:
                if activity.code == activity_code:
                    return activity
    return None


@router.get("/activities", response_model=list[TenantActivityPricing])
def selected_activities(user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    return repository.list_tenant_pricing(user.company_id)


@router.put("/activities/{activity_code}", response_model=TenantActivityPricing)
def set_activity_override(activity_code: str, payload: TenantActivityOverrideRequest, request: Request, user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    activity = _activity_by_code(request, activity_code)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    if payload.selected_pricing_method_code not in activity.available_pricing_method_codes:
        raise HTTPException(status_code=422, detail="Pricing method is not available for this activity")
    known_charges = {charge.code for charge in request.app.state.catalogue.additional_charges}
    if not set(payload.enabled_additional_charge_codes).issubset(known_charges):
        raise HTTPException(status_code=422, detail="Unknown additional charge")
    return repository.save_tenant_pricing(user.company_id, activity_code, payload)


@router.delete("/activities/{activity_code}/override")
def reset_activity_to_system_default(activity_code: str, request: Request, user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    if not _activity_by_code(request, activity_code):
        raise HTTPException(status_code=404, detail="Activity not found")
    repository.reset_tenant_pricing(user.company_id, activity_code)
    return {"reset": True, "system_default_preserved": True}
