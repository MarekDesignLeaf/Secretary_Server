"""Authentication primitives for the clean Secretary backend foundation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 30
REFRESH_TOKEN_DAYS = 30
_PASSWORD_ITERATIONS = 210_000


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2 without relying on legacy app code."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        _PASSWORD_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_raw)
        expected = base64.b64decode(digest_raw)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _jwt_secret() -> str:
    secret = os.getenv("SECRETARY_CLEAN_JWT_SECRET") or os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("SECRETARY_CLEAN_JWT_SECRET or JWT_SECRET is required")
    return secret


def issue_token_pair(*, user_id: str, company_id: str, role: str) -> TokenPair:
    now = datetime.now(timezone.utc)
    subject = {"sub": user_id, "company_id": company_id, "role": role}
    access_payload: dict[str, Any] = {
        **subject,
        "token_use": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    refresh_payload: dict[str, Any] = {
        **subject,
        "token_use": "refresh",
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_DAYS),
        "jti": secrets.token_urlsafe(18),
    }
    return TokenPair(
        access_token=jwt.encode(access_payload, _jwt_secret(), algorithm=JWT_ALGORITHM),
        refresh_token=jwt.encode(refresh_payload, _jwt_secret(), algorithm=JWT_ALGORITHM),
    )


def decode_token(token: str, *, expected_use: str) -> dict[str, Any]:
    payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    if payload.get("token_use") != expected_use:
        raise jwt.InvalidTokenError(f"Expected {expected_use} token")
    return payload
