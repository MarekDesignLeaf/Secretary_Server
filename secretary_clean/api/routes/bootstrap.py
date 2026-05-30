from __future__ import annotations

import importlib.metadata
import os

from fastapi import APIRouter, Depends, HTTPException, Request

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
def get_version(request: Request):
    """Return server version info. No authentication required."""
    try:
        ver = importlib.metadata.version("secretary-clean")
    except importlib.metadata.PackageNotFoundError:
        ver = "1.0.0"
    repo = getattr(request.app.state, "repository", None)
    storage = "postgresql" if repo and "Postgres" in type(repo).__name__ else "in_memory"
    db_url_set = bool(os.environ.get("DATABASE_URL"))
    return {
        "server_version": ver,
        "api_version": "v1",
        "backend": "secretary_clean",
        "storage_type": storage,
        "database_url_set": db_url_set,
        "warning": None if storage == "postgresql" else "DATABASE_URL not configured — data will be lost on restart!",
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
def create_first_install(
    request: Request,
    payload: FirstInstallCreate,
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        # Build activity_defaults from in-memory catalogue so postgres repo can seed pricing
        activity_defaults: dict[str, str] = {}
        catalogue = getattr(getattr(request, "app", None), "state", None)
        if catalogue:
            cat = getattr(catalogue, "catalogue", None)
            if cat:
                for industry in getattr(cat, "industries", []):
                    for subtype in getattr(industry, "subtypes", []):
                        for activity in getattr(subtype, "activities", []):
                            code = getattr(activity, "code", None)
                            method = getattr(activity, "default_pricing_method_code", None)
                            if code and method:
                                activity_defaults[code] = method
        return repository.create_first_install(payload, activity_defaults=activity_defaults)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/wipe")
def wipe_all_data(repository: InMemorySecretaryRepository = Depends(get_repository)):
    """Wipe ALL company/user data for a clean factory reset.

    Only works when the ALLOW_WIPE environment variable is set to 'true' on the server.
    After wiping, the app will go back to the first-install onboarding flow.
    """
    if os.getenv("ALLOW_WIPE", "").lower() != "true":
        raise HTTPException(
            status_code=403,
            detail="Factory reset is not enabled on this server. Set ALLOW_WIPE=true to enable.",
        )
    try:
        repository.wipe_all_data()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Wipe failed: {exc}") from exc
    return {"ok": True, "is_ready": False}
