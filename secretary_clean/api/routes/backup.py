"""Backup / uninstall endpoints for Secretary Clean.

Permission rules
----------------
* backup.manage (admin/owner only)
    POST /backup/create  →  full scope: all users + DB reference
    GET  /backup/manifests   →  list all company backups
    GET  /backup/restore/{token}  →  download any backup by token

* backup.personal (all authenticated roles)
    POST /backup/create  →  personal scope: caller's credentials only
    GET  /backup/restore/{token}  →  only if caller owns the backup

The Android client calls POST /backup/create before uninstall, stores the
returned BackupManifest in install_data/ on-device, and optionally uploads it
to the server (storage_location = 'server' | 'both').
"""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from secretary_clean.api.deps import current_user, get_repository, require_permission
from secretary_clean.core.models import (
    BackupCreateRequest,
    BackupManifest,
    BackupRestoreInfo,
    BackupScope,
    BackupStorageLocation,
    BackupUserCredential,
    Permission,
    UserAccount,
)

router = APIRouter(prefix="/backup", tags=["backup"])

_RESTORE_TOKEN_TTL_DAYS = 90


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# POST /backup/create
# ---------------------------------------------------------------------------

@router.post("/create", response_model=BackupManifest)
def create_backup(
    payload: BackupCreateRequest,
    user: UserAccount = Depends(current_user),
    repository=Depends(get_repository),
):
    """Create a pre-uninstall backup.

    Admins/owners get *full* scope (all users + DB reference).
    Everyone else gets *personal* scope (own credentials only).
    """
    has_full = Permission.backup_manage in set(user.permissions)
    has_personal = Permission.backup_personal in set(user.permissions)

    if not has_full and not has_personal:
        raise HTTPException(status_code=403, detail="No backup permission")

    scope = BackupScope.full if has_full else BackupScope.personal

    # ----- collect users -------------------------------------------------
    if scope == BackupScope.full:
        all_users = repository.list_users(company_id=user.company_id)
    else:
        # personal: only the calling user
        me = repository.get_user(user.id)
        all_users = [me] if me else []

    # ----- collect biometric hashes per user -----------------------------
    user_credentials: list[BackupUserCredential] = []
    for u in all_users:
        bio_hashes = repository.get_biometric_hashes(user_id=u.id)
        user_credentials.append(
            BackupUserCredential(
                user_id=u.id,
                email=u.email,
                display_name=u.display_name,
                role=u.role.value,
                biometric_hashes=bio_hashes,
            )
        )

    # ----- settings snapshot (company) -----------------------------------
    company = repository.get_company(user.company_id)
    settings: dict = {}
    if company:
        settings = {
            "legal_name": company.legal_name,
            "trading_name": company.trading_name,
            "default_country": company.default_country,
            "default_currency": company.default_currency,
            "timezone": company.timezone,
            "industry_group": company.industry_group,
            "industry_subtype": company.industry_subtype,
        }

    # ----- DB reference (full scope only) --------------------------------
    db_reference: str | None = None
    if scope == BackupScope.full:
        db_reference = os.getenv("DATABASE_URL")  # Railway injects this

    # ----- restore token (if server storage) -----------------------------
    restore_token: str | None = None
    restore_expires: datetime | None = None
    if payload.storage_location in (BackupStorageLocation.server, BackupStorageLocation.both):
        restore_token = secrets.token_urlsafe(32)
        restore_expires = _utcnow() + timedelta(days=_RESTORE_TOKEN_TTL_DAYS)

    backup_id = str(uuid.uuid4())
    now = _utcnow()
    company_name = company.legal_name if company else ""

    manifest = BackupManifest(
        backup_id=backup_id,
        created_at=now,
        created_by_user_id=user.id,
        created_by_role=user.role.value,
        backup_scope=scope,
        company_id=user.company_id,
        company_legal_name=company_name,
        users=user_credentials,
        settings=settings,
        db_reference=db_reference,
        restore_token=restore_token,
        restore_token_expires_at=restore_expires,
    )

    # ----- persist on server if requested --------------------------------
    if payload.storage_location in (BackupStorageLocation.server, BackupStorageLocation.both):
        repository.save_backup_manifest(
            backup_id=backup_id,
            company_id=user.company_id,
            created_by_user_id=user.id,
            created_by_role=user.role.value,
            backup_scope=scope.value,
            includes_db_reference=(db_reference is not None),
            storage_location=payload.storage_location.value,
            restore_token=restore_token,
            restore_token_expires_at=restore_expires,
            payload=manifest.model_dump(mode="json"),
        )

    return manifest


# ---------------------------------------------------------------------------
# GET /backup/manifests  (admin only)
# ---------------------------------------------------------------------------

@router.get("/manifests", response_model=list[BackupRestoreInfo])
def list_manifests(
    user: UserAccount = Depends(require_permission(Permission.backup_manage)),
    repository=Depends(get_repository),
):
    """List all server-stored backup manifests for this company."""
    rows = repository.list_backup_manifests(company_id=user.company_id)
    return rows


# ---------------------------------------------------------------------------
# GET /backup/restore/{token}
# ---------------------------------------------------------------------------

@router.get("/restore/{token}", response_model=BackupManifest)
def get_backup_by_token(
    token: str,
    user: UserAccount = Depends(current_user),
    repository=Depends(get_repository),
):
    """Download a backup by restore token.

    Admin/owner can download any backup.
    Regular users can only download their own personal backup.
    """
    manifest_row = repository.get_backup_manifest_by_token(token)
    if not manifest_row:
        raise HTTPException(status_code=404, detail="Backup not found or token expired")

    # Enforce ownership for non-admins
    has_full = Permission.backup_manage in set(user.permissions)
    if not has_full and manifest_row["created_by_user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Check token expiry
    expires = manifest_row.get("restore_token_expires_at")
    if expires and _utcnow() > expires:
        raise HTTPException(status_code=410, detail="Restore token has expired")

    return BackupManifest(**manifest_row["payload"])


# ---------------------------------------------------------------------------
# POST /backup/biometric/register  (any authenticated user)
# ---------------------------------------------------------------------------

class BiometricRegisterRequest(BaseModel):
    device_id: str
    biometric_hash: str
    label: str | None = None


@router.post("/biometric/register")
def register_biometric(
    payload: BiometricRegisterRequest,
    user: UserAccount = Depends(current_user),
    repository=Depends(get_repository),
):
    """Store a fingerprint hash for the current user on a specific device."""
    bio_id = str(uuid.uuid4())
    repository.save_biometric(
        bio_id=bio_id,
        user_id=user.id,
        device_id=payload.device_id,
        biometric_hash=payload.biometric_hash,
        label=payload.label,
    )
    return {"status": "ok", "biometric_id": bio_id}


# ---------------------------------------------------------------------------
# DELETE /backup/biometric/{device_id}  (own devices only)
# ---------------------------------------------------------------------------

@router.delete("/biometric/{device_id}")
def remove_biometric(
    device_id: str,
    user: UserAccount = Depends(current_user),
    repository=Depends(get_repository),
):
    """Deactivate a stored fingerprint hash for a device."""
    removed = repository.deactivate_biometric(user_id=user.id, device_id=device_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Biometric entry not found")
    return {"status": "ok"}
