from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.models import CreateUserRequest, ResetPasswordRequest, UpdateUserRequest, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserAccount])
def list_users(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return repository.list_users(user.company_id)


@router.get("/roles")
def list_roles(repository: InMemorySecretaryRepository = Depends(get_repository)):
    return repository.list_roles()


@router.post("", response_model=UserAccount)
def create_user(
    payload: CreateUserRequest,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    if user.role.value not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    try:
        return repository.create_user(
            company_id=user.company_id,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            role=payload.role,
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
            preferred_language_code=payload.preferred_language_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{user_id}", response_model=UserAccount)
def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    if user.role.value not in ("owner", "admin") and user.id != user_id:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    try:
        return repository.update_user(
            user_id,
            user.company_id,
            display_name=payload.display_name,
            role=payload.role,
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
            preferred_language_code=payload.preferred_language_code,
            is_active=payload.is_active,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{user_id}")
def delete_user(
    user_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    if user.role.value not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    try:
        repository.delete_user(user_id, user.company_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    payload: ResetPasswordRequest,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    if user.role.value not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    try:
        repository.reset_user_password(user_id, payload.new_password)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Reset user password to default temp password '12345' and set must_change_password=True."""
    if user.role.value not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # Verify user belongs to same company
    target = repository.get_user(user_id)
    if not target or target.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="User not found")
    repository.reset_user_password(user_id, "12345")
    return {"ok": True}
