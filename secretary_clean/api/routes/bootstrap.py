from __future__ import annotations

import importlib.metadata

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import get_repository
from secretary_clean.core.models import BootstrapStatus, FirstAdminCreate, FirstCompanyCreate, FirstInstallCreate, FirstInstallResult, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])

# ---------------------------------------------------------------------------
# Version endpoint — no auth required, used by Settings screen
# ---------------------------------------------------------------------------

# Separate router so we can mount it at /api/v1 without the /bootstrap prefix
version_router = APIRouter(tags=["version"])

@version_router.get("/version")
def get_version():
    """Return server version info. No authentication required."""
    try:
        ver = importlib.metadata.version("secretary-clean")
    except importlib.metadata.PackageNotFoundError:
        ver = "1.0.0"
    return {
        "server_version": ver,
        "api_version": "v1",
        "backend": "secretary_clean",
    }


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
def create_first_install(payload: FirstInstallCreate, repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        return repository.create_first_install(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
