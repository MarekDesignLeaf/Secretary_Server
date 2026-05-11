from __future__ import annotations

import logging
import os

import jwt
from fastapi import APIRouter, Depends, HTTPException

from secretary_clean.api.deps import current_user, get_repository
from secretary_clean.core.email import is_dev_mode, send_password_reset_email
from secretary_clean.core.models import (
    AdminRecoveryPayload,
    GenericSuccessResponse,
    LoginRequest,
    PasswordResetConfirmPayload,
    PasswordResetRequestPayload,
    RefreshRequest,
    UserAccount,
)
from secretary_clean.core.repository import InMemorySecretaryRepository
from secretary_clean.core.security import (
    TokenPair,
    decode_token,
    generate_reset_token,
    issue_token_pair,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


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


# ── Password reset ──────────────────────────────────────────────────────────

@router.post("/password-reset/request", response_model=GenericSuccessResponse)
def password_reset_request(
    payload: PasswordResetRequestPayload,
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Always returns 200 regardless of whether the email exists (no user enumeration)."""
    user = repository.get_user_by_email(payload.email)
    if user and user.is_active:
        plain_token = generate_reset_token()
        repository.create_password_reset_token(user, plain_token)
        try:
            send_password_reset_email(to_email=user.email, reset_token=plain_token)
        except RuntimeError as exc:
            logger.error("Failed to send reset email: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to send reset email") from exc

        if is_dev_mode():
            return GenericSuccessResponse(
                ok=True,
                message="DEV MODE -- reset token: " + plain_token,
            )

    return GenericSuccessResponse(
        ok=True,
        message="If that email is registered, a reset link has been sent.",
    )


@router.post("/password-reset/confirm", response_model=GenericSuccessResponse)
def password_reset_confirm(
    payload: PasswordResetConfirmPayload,
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    token_record = repository.verify_password_reset_token(payload.token)
    if not token_record:
        raise HTTPException(status_code=400, detail="Invalid, expired, or already used token")
    repository.reset_user_password(token_record.user_id, payload.new_password)
    repository.mark_password_reset_token_used(token_record.id)
    return GenericSuccessResponse(message="Password has been reset successfully.")


# ── Emergency admin recovery ────────────────────────────────────────────────

@router.post("/recovery/admin-reset", response_model=GenericSuccessResponse)
def admin_recovery_reset(
    payload: AdminRecoveryPayload,
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    """Emergency owner/admin password reset using SECRETARY_RECOVERY_KEY env var."""
    env_key = os.getenv("SECRETARY_RECOVERY_KEY", "")
    if not env_key:
        raise HTTPException(
            status_code=403,
            detail="Emergency recovery is not enabled on this server",
        )
    if payload.recovery_key != env_key:
        raise HTTPException(status_code=403, detail="Invalid recovery key")
    try:
        user = repository.admin_recovery_reset_password(payload.admin_email, payload.new_password)
    except KeyError:
        raise HTTPException(status_code=404, detail="Admin account not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    logger.info("Emergency recovery used for account %s", user.id)
    return GenericSuccessResponse(message="Password reset for " + user.email)
