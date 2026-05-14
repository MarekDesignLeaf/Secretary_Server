from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.models import (
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    RefreshRequest,
    UserAccount,
)
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core.security import TokenPair, decode_token, issue_token_pair

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, repository: InMemorySecretaryRepository = Depends(get_repository)):
    user = repository.authenticate(payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return issue_token_pair(user_id=user.id, company_id=user.company_id, role=user.role.value)


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, repository: InMemorySecretaryRepository = Depends(get_repository)):
    try:
        token_payload = decode_token(payload.refresh_token, expected_use="refresh")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc
    user = repository.get_user(token_payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or missing")
    return issue_token_pair(user_id=user.id, company_id=user.company_id, role=user.role.value)


@router.get("/me", response_model=UserAccount)
def me(user: UserAccount = Depends(current_user)):
    return user


@router.get("/roles")
def roles(repository: InMemorySecretaryRepository = Depends(get_repository)):
    return repository.list_roles()


@router.post("/register", response_model=UserAccount)
def register(
    payload: CreateUserRequest,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Create a new user in the same company. Requires admin/owner."""
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


@router.put("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    ok = repository.change_password(user.id, payload.current_password, payload.new_password)
    if not ok:
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    return {"ok": True}


@router.get("/first-login-users")
def first_login_users():
    """Compatibility stub — returns empty list on clean backend."""
    return []
