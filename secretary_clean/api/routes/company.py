from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository
from pydantic import BaseModel

from secretary_clean.core.models import CompanyLegalIdentity, CompanyOperatingSettings, CompanyProfile, TenantIndustriesUpdate, TenantIndustry, TenantOperatingProfile, UserAccount


class IndustryUpdate(BaseModel):
    industry_group: str | None = None
    industry_subtype: str | None = None
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/company", tags=["company"])


@router.get("/profile", response_model=CompanyProfile)
def get_profile(user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    company = repository.get_company(user.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.put("/profile", response_model=CompanyProfile)
def update_profile(payload: CompanyProfile, user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        return repository.update_company(user.company_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.get("/legal-identity", response_model=CompanyLegalIdentity)
def get_legal_identity(user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        return repository.get_company_legal_identity(user.company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.put("/legal-identity", response_model=CompanyLegalIdentity)
def update_legal_identity(payload: CompanyLegalIdentity, user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        return repository.update_company_legal_identity(user.company_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.get("/operating-settings", response_model=CompanyOperatingSettings)
def get_operating_settings(user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    return repository.get_company_settings(user.company_id)


@router.put("/operating-settings", response_model=CompanyOperatingSettings)
def update_operating_settings(payload: CompanyOperatingSettings, user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        return repository.update_company_settings(user.company_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.get("/operating-profile", response_model=TenantOperatingProfile)
def get_operating_profile(user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        return repository.get_tenant_operating_profile(user.company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.put("/industry", response_model=TenantOperatingProfile)
def update_industry(payload: IndustryUpdate, user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    """Update the company's industry group and subtype after first install."""
    try:
        return repository.update_company_industry(
            user.company_id,
            industry_group=payload.industry_group,
            industry_subtype=payload.industry_subtype,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.get("/industries", response_model=list[TenantIndustry])
def get_industries(user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    """Phase A1: return all industries assigned to the tenant (multi-industry).

    Backward compatible: if only a legacy single industry exists, it is returned
    as a one-element list with is_primary=True."""
    try:
        return repository.get_tenant_industries(user.company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.put("/industries", response_model=list[TenantIndustry])
def set_industries(payload: TenantIndustriesUpdate, user: UserAccount = Depends(current_user), repository: InMemorySecretaryRepository = Depends(get_repository)):
    """Phase A1: replace the full set of industries for the tenant.

    The first industry (or the one flagged is_primary) becomes the primary and
    is mirrored into the legacy industry_group/subtype fields for compatibility."""
    try:
        return repository.set_tenant_industries(user.company_id, payload.industries)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc
