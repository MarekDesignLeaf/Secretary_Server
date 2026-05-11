"""
tool_secret_store.py
Encryption/decryption helpers for tenant_tool_secret.
Uses Fernet symmetric encryption (cryptography package).
Key is loaded from environment variable TOOL_SECRET_KEY or a local key file.

SECURITY RULES:
  - Never log plain-text values.
  - Never return plain-text values to API responses — only masked previews.
  - Always use mask_secret() before logging.
"""

import base64
import hashlib
import os
from functools import lru_cache

try:
    from cryptography.fernet import Fernet, InvalidToken
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False


# ── Key management ────────────────────────────────────────────────────────────
_LOCAL_KEY_FILE = os.path.join(os.path.dirname(__file__), ".tool_secret_key")


@lru_cache(maxsize=8)
def _get_fernet(key_ref: str = "local_v1") -> "Fernet":
    """
    Return a Fernet instance for the given key_ref.
    Key sources (in priority order):
      1. Env var TOOL_SECRET_KEY_{KEY_REF.upper()}
      2. Env var TOOL_SECRET_KEY  (generic fallback)
      3. .tool_secret_key file next to this module
      4. Auto-generate + persist to .tool_secret_key (dev only)
    """
    if not _FERNET_AVAILABLE:
        raise RuntimeError(
            "cryptography package not installed. "
            "Run: pip install cryptography"
        )

    env_key = (
        os.environ.get(f"TOOL_SECRET_KEY_{key_ref.upper().replace('-','_')}")
        or os.environ.get("TOOL_SECRET_KEY")
    )
    if env_key:
        # Accept either raw bytes or base64url-encoded Fernet key
        raw = env_key.strip().encode() if isinstance(env_key, str) else env_key
        return Fernet(raw)

    if os.path.exists(_LOCAL_KEY_FILE):
        key = open(_LOCAL_KEY_FILE, "rb").read().strip()
        return Fernet(key)

    # Dev: auto-generate and persist
    key = Fernet.generate_key()
    open(_LOCAL_KEY_FILE, "wb").write(key)
    os.chmod(_LOCAL_KEY_FILE, 0o600)
    return Fernet(key)


# ── Encrypt / decrypt ─────────────────────────────────────────────────────────
def encrypt_secret(plain_value: str, key_ref: str = "local_v1") -> str:
    """
    Encrypt a plain-text secret.
    Returns base64-encoded encrypted string safe for DB storage.
    """
    f = _get_fernet(key_ref)
    return f.encrypt(plain_value.encode()).decode()


def decrypt_secret(encrypted_value: str, key_ref: str = "local_v1") -> str:
    """
    Decrypt a value from tenant_tool_secret.
    Raises RuntimeError on decryption failure.
    """
    try:
        f = _get_fernet(key_ref)
        return f.decrypt(encrypted_value.encode()).decode()
    except Exception as exc:
        raise RuntimeError(f"Failed to decrypt secret (key_ref={key_ref}): {exc}") from exc


def rotate_secret(encrypted_value: str, old_key_ref: str, new_key_ref: str) -> str:
    """Re-encrypt a secret with a new key. Used for key rotation."""
    plain = decrypt_secret(encrypted_value, old_key_ref)
    return encrypt_secret(plain, new_key_ref)


# ── Masking ───────────────────────────────────────────────────────────────────
def mask_secret(value: str, show_chars: int = 8) -> str:
    """
    Return a masked version safe for logging/display.
    e.g. sk_live_123456789abcdef → sk_live_1********
    """
    if not value:
        return "***"
    visible = min(show_chars, len(value))
    return value[:visible] + "*" * max(4, len(value) - visible)


# ── DB helpers ────────────────────────────────────────────────────────────────
def save_secret(conn, tenant_id: int, tool_id: str, slot_name: str,
                plain_value: str, updated_by: int | None = None,
                key_ref: str = "local_v1"):
    """Encrypt and upsert a secret into tenant_tool_secret."""
    encrypted = encrypt_secret(plain_value, key_ref)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO crm.tenant_tool_secret
                (tenant_id, tool_id, slot_name, encrypted_value, encryption_key_ref,
                 is_active, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, TRUE, %s, NOW())
            ON CONFLICT ON CONSTRAINT uq_tenant_tool_secret
            DO UPDATE SET
                encrypted_value    = EXCLUDED.encrypted_value,
                encryption_key_ref = EXCLUDED.encryption_key_ref,
                is_active          = TRUE,
                updated_by         = EXCLUDED.updated_by,
                updated_at         = NOW()
        """, (tenant_id, tool_id, slot_name, encrypted, key_ref, updated_by))


def load_secret(conn, tenant_id: int, tool_id: str, slot_name: str) -> str | None:
    """Load and decrypt a secret from tenant_tool_secret. Returns None if not found."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT encrypted_value, encryption_key_ref
              FROM crm.tenant_tool_secret
             WHERE tenant_id = %s AND tool_id = %s AND slot_name = %s AND is_active = TRUE
        """, (tenant_id, tool_id, slot_name))
        row = cur.fetchone()
    if not row:
        return None
    return decrypt_secret(row["encrypted_value"], row["encryption_key_ref"])


def save_config(conn, tenant_id: int, tool_id: str, slot_name: str,
                value_text: str | None = None, value_json: dict | None = None,
                updated_by: int | None = None):
    """Upsert a plain config value into tenant_tool_config."""
    import json as _json
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO crm.tenant_tool_config
                (tenant_id, tool_id, slot_name, value_text, value_json,
                 is_active, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, TRUE, %s, NOW())
            ON CONFLICT ON CONSTRAINT uq_tenant_tool_config
            DO UPDATE SET
                value_text = EXCLUDED.value_text,
                value_json = EXCLUDED.value_json,
                is_active  = TRUE,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
        """, (
            tenant_id, tool_id, slot_name, value_text,
            _json.dumps(value_json) if value_json else None,
            updated_by,
        ))
