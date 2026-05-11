"""API dependencies for the clean Secretary foundation."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request

from secretary_clean.core.models import Permission, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core.security import decode_token


def get_repository(request: Request) -> InMemorySecretaryRepository:
    return request.app.state.repository


def current_user(
    repository: Annotated[InMemorySecretaryRepository, Depends(get_repository)],
    authorization: Annotated[str | None, Header()] = None,
) -> UserAccount:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token, expected_use="access")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc
    user = repository.get_user(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or missing")
    return user


def require_permission(permission: Permission):
    def dependency(user: UserAccount = Depends(current_user)) -> UserAccount:
        if permission not in set(user.permissions):
            raise HTTPException(status_code=403, detail=f"Permission required: {permission.value}")
        return user

    return dependency
