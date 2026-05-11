from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from secretary_clean.api.deps import get_repository
from secretary_clean.core.models import BootstrapStatus, FirstAdminCreate, FirstCompanyCreate, FirstInstallCreate, FirstInstallResult, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


@router.get("/status", response_model=BootstrapStatus)
def bootstrap_status(repository: InMemorySecretaryRepository = Depends(get_repository)):
    return repository.bootstrap_status()


@router.post("/first-company")
def create_first_company(payload: FirstCompanyCreate, repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        return repository.create_first_company(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/first-admin", response_model=UserAccount)
def create_first_admin(payload: FirstAdminCreate, repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        return repository.create_first_admin(
            company_id=payload.company_id,
            email=payload.email,
            display_name=payload.display_name,
            password=payload.password,
            preferred_language_code=payload.preferred_language_code,
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/first-install", response_model=FirstInstallResult)
def create_first_install(
    payload: FirstInstallCreate,
    request: Request,
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        # Build activity_defaults map from the catalogue loaded at startup
        catalogue = getattr(request.app.state, "catalogue", None)
        activity_defaults: dict[str, str] = {}
        if catalogue is not None:
            for industry in catalogue.industries:
                for subtype in industry.subtypes:
                    for activity in subtype.activities:
                        activity_defaults[activity.code] = activity.default_pricing_method_code
        return repository.create_first_install(payload, activity_defaults=activity_defaults)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
