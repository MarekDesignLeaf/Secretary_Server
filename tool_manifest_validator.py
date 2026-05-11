"""
tool_manifest_validator.py
Validates a tool package manifest.json against the required schema.
Enforces security rules: no plain-text secrets, required slots present, etc.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

# ── Required top-level fields in manifest.json ───────────────────────────────
MANIFEST_REQUIRED_FIELDS = [
    "tool_id", "tool_name", "description", "version", "author",
    "risk_level", "entry_point", "install_script", "uninstall_script",
    "required_permissions", "required_config_slots", "required_secret_slots",
    "voice_commands", "supported_platforms", "license_type",
]

# ── Required fields for each config_slot definition ──────────────────────────
SLOT_REQUIRED_FIELDS = [
    "slot_name", "display_name", "required", "secret",
    "replaceable", "validation_type", "stored_in",
]

# ── Slot names that MUST be marked secret=True ───────────────────────────────
MUST_BE_SECRET = {
    "api_key", "api_secret", "access_token", "refresh_token",
    "client_secret", "private_key", "signing_secret", "webhook_secret",
    "password", "token", "bearer_token", "auth_token",
}

# ── Values that are forbidden in any manifest field (plain secret patterns) ──
FORBIDDEN_PLAIN_SECRET_PATTERNS = [
    r"sk_live_[A-Za-z0-9]{10,}",   # Stripe-style live key
    r"sk_test_[A-Za-z0-9]{10,}",   # Stripe-style test key
    r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}",  # Bearer token
    r"[Aa][Pp][Ii][_-]?[Kk][Ee][Yy]\s*[:=]\s*['\"][A-Za-z0-9]{16,}",
]

VALID_RISK_LEVELS    = {"low", "medium", "high", "critical"}
VALID_VALIDATION_TYPES = {"text", "url", "email", "regex", "json", "enum", "integer", "boolean"}
VALID_SCOPES         = {"tenant", "user", "system"}
VALID_STORED_IN      = {"tenant_tool_config", "tenant_tool_secret"}
VALID_EXPORT_MODES   = {"empty_slots", "encrypted_secrets", "no_secrets"}
VALID_INSTALL_STATUS = {
    "available", "installed", "configured", "configuration_required",
    "connection_failed", "enabled", "disabled", "uninstalled",
}


class ValidationError(Exception):
    """Raised when manifest validation fails."""


class ManifestValidator:
    def __init__(self, manifest: dict, config_schema: dict | None = None):
        self.manifest      = manifest
        self.config_schema = config_schema or {}
        self.errors: list[str]    = []
        self.warnings: list[str]  = []

    # ── Public entry point ────────────────────────────────────────────────────
    def validate(self) -> dict:
        """
        Run all validation checks.
        Returns: {"valid": bool, "errors": [...], "warnings": [...]}
        """
        self._check_required_fields()
        self._check_tool_id_format()
        self._check_version_format()
        self._check_risk_level()
        self._check_no_plain_secrets_in_manifest()
        self._check_secret_slots()
        self._check_config_schema()
        self._check_voice_commands()
        self._check_replaceable_rule()
        return {
            "valid":    len(self.errors) == 0,
            "errors":   self.errors,
            "warnings": self.warnings,
        }

    # ── Checks ────────────────────────────────────────────────────────────────
    def _check_required_fields(self):
        for field in MANIFEST_REQUIRED_FIELDS:
            if field not in self.manifest:
                self.errors.append(f"Missing required field: '{field}'")

    def _check_tool_id_format(self):
        tool_id = self.manifest.get("tool_id", "")
        if not re.match(r"^[a-z][a-z0-9_]{1,63}$", tool_id):
            self.errors.append(
                f"tool_id '{tool_id}' must be lowercase snake_case, 2-64 chars, start with letter"
            )

    def _check_version_format(self):
        version = self.manifest.get("version", "")
        if not re.match(r"^\d+\.\d+\.\d+$", version):
            self.errors.append(f"version '{version}' must be semantic (e.g. 1.0.0)")

    def _check_risk_level(self):
        rl = self.manifest.get("risk_level", "")
        if rl not in VALID_RISK_LEVELS:
            self.errors.append(f"risk_level '{rl}' must be one of {VALID_RISK_LEVELS}")

    def _check_no_plain_secrets_in_manifest(self):
        """Scan entire manifest JSON for patterns that look like plain-text API keys."""
        manifest_str = json.dumps(self.manifest)
        for pattern in FORBIDDEN_PLAIN_SECRET_PATTERNS:
            if re.search(pattern, manifest_str):
                self.errors.append(
                    f"SECURITY: manifest appears to contain a plain-text secret "
                    f"(matched pattern: {pattern}). Never embed API keys in manifest.json."
                )

    def _check_secret_slots(self):
        """Slots in MUST_BE_SECRET set must have secret=True and replaceable=True."""
        slots: list[dict] = self.manifest.get("required_secret_slots", [])
        for slot in slots:
            name = slot.get("slot_name", "")
            # Any slot whose name matches a known-secret pattern must be secret
            for must_secret_name in MUST_BE_SECRET:
                if must_secret_name in name.lower():
                    if not slot.get("secret", False):
                        self.errors.append(
                            f"Slot '{name}' matches a secret keyword but secret=False. "
                            f"Set secret=True."
                        )
                    break
            if not slot.get("replaceable", True):
                self.errors.append(
                    f"Secret slot '{name}' must have replaceable=True. "
                    f"API values must always be replaceable."
                )
            # stored_in must be tenant_tool_secret
            stored_in = slot.get("stored_in", "")
            if slot.get("secret") and stored_in != "tenant_tool_secret":
                self.errors.append(
                    f"Secret slot '{name}' must have stored_in='tenant_tool_secret', "
                    f"got '{stored_in}'."
                )

    def _check_config_schema(self):
        """Validate config_schema.json slot definitions."""
        slots: list[dict] = self.config_schema.get("slots", [])
        for slot in slots:
            for field in SLOT_REQUIRED_FIELDS:
                if field not in slot:
                    self.errors.append(
                        f"config_schema slot '{slot.get('slot_name','?')}' "
                        f"missing required field '{field}'"
                    )
            vtype = slot.get("validation_type", "")
            if vtype not in VALID_VALIDATION_TYPES:
                self.warnings.append(
                    f"Slot '{slot.get('slot_name')}' has unknown validation_type '{vtype}'"
                )
            scope = slot.get("scope", "tenant")
            if scope not in VALID_SCOPES:
                self.warnings.append(
                    f"Slot '{slot.get('slot_name')}' has unknown scope '{scope}'"
                )
            stored = slot.get("stored_in", "")
            if stored and stored not in VALID_STORED_IN:
                self.errors.append(
                    f"Slot '{slot.get('slot_name')}' has invalid stored_in='{stored}'. "
                    f"Must be one of {VALID_STORED_IN}."
                )
            # Secret consistency
            is_secret = slot.get("secret", False)
            expected_stored = "tenant_tool_secret" if is_secret else "tenant_tool_config"
            if stored and stored != expected_stored:
                self.errors.append(
                    f"Slot '{slot.get('slot_name')}': secret={is_secret} but "
                    f"stored_in='{stored}' (expected '{expected_stored}')"
                )

    def _check_voice_commands(self):
        cmds = self.manifest.get("voice_commands", [])
        if not isinstance(cmds, list):
            self.errors.append("voice_commands must be a list")

    def _check_replaceable_rule(self):
        """All secret slots must be replaceable."""
        for slots_key in ("required_secret_slots", "required_config_slots"):
            for slot in self.manifest.get(slots_key, []):
                if slot.get("secret") and not slot.get("replaceable", True):
                    self.errors.append(
                        f"Slot '{slot.get('slot_name')}' is secret but replaceable=False. "
                        f"All secret slots must be replaceable."
                    )


# ── Package checksum ──────────────────────────────────────────────────────────
def compute_package_checksum(package_dir: Path) -> str:
    """
    Compute SHA-256 checksum of all package files (sorted by name).
    Used to populate package_checksum.txt and verify on install.
    """
    hasher = hashlib.sha256()
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and path.name != "package_checksum.txt":
            hasher.update(path.name.encode())
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def verify_package_checksum(package_dir: Path, expected: str) -> bool:
    actual = compute_package_checksum(package_dir)
    return actual == expected


# ── Convenience wrapper ───────────────────────────────────────────────────────
def validate_manifest_file(manifest_path: str, schema_path: str | None = None) -> dict:
    """
    Validate a manifest.json file (and optionally its config_schema.json).
    Returns: {"valid": bool, "errors": [...], "warnings": [...]}
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    config_schema = {}
    if schema_path:
        config_schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = ManifestValidator(manifest, config_schema)
    return validator.validate()


def validate_manifest_dict(manifest: dict, config_schema: dict | None = None) -> dict:
    validator = ManifestValidator(manifest, config_schema or {})
    return validator.validate()


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python tool_manifest_validator.py <manifest.json> [config_schema.json]")
        sys.exit(1)
    schema = sys.argv[2] if len(sys.argv) > 2 else None
    result = validate_manifest_file(sys.argv[1], schema)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["valid"] else 1)
