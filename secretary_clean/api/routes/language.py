from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository, require_permission
from secretary_clean.core.language import AVAILABLE_LANGUAGES, normalize_language_code, resolve_language_context
from secretary_clean.core.models import (
    ClientLanguageSettings,
    ClientLanguageUpdate,
    LanguageDefinition,
    LanguageSettings,
    Permission,
    TenantLanguage,
    TenantLanguageUpdate,
    TenantOperatingProfile,
    UserAccount,
)
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/language", tags=["language"])


@router.get("/settings", response_model=TenantOperatingProfile)
def get_language_settings(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.get_tenant_operating_profile(user.company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.put("/settings", response_model=TenantOperatingProfile)
def put_language_settings(
    payload: LanguageSettings,
    user: UserAccount = Depends(require_permission(Permission.language_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.update_tenant_operating_profile(user.company_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.get("/available", response_model=list[LanguageDefinition])
def available_languages():
    return list(AVAILABLE_LANGUAGES)


@router.get("/tenant", response_model=list[TenantLanguage])
def get_tenant_languages(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return repository.list_tenant_languages(user.company_id)


@router.put("/tenant", response_model=list[TenantLanguage])
def put_tenant_languages(
    payload: TenantLanguageUpdate,
    user: UserAccount = Depends(require_permission(Permission.language_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.replace_tenant_languages(user.company_id, payload.languages)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc


@router.get("/client/{client_id}", response_model=ClientLanguageSettings)
def get_client_language(
    client_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.get_client_language(user.company_id, client_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Client not found") from exc


@router.put("/client/{client_id}", response_model=ClientLanguageSettings)
def put_client_language(
    client_id: str,
    payload: ClientLanguageUpdate,
    user: UserAccount = Depends(require_permission(Permission.language_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        return repository.set_client_language(
            user.company_id,
            client_id,
            normalize_language_code(payload.preferred_language_code),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Client not found") from exc


@router.get("/context")
def get_language_context(
    client_id: str | None = None,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    profile = repository.get_tenant_operating_profile(user.company_id)
    client_language = repository.get_client_preferred_language_code(user.company_id, client_id)
    return resolve_language_context(profile=profile, user=user, client_language_code=client_language)
