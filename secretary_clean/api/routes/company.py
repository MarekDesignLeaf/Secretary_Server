from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.models import CompanyLegalIdentity, CompanyOperatingSettings, CompanyProfile, TenantOperatingProfile, UserAccount
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
