"""
tool_installer.py
Installs a tool package (.zip) into the CRM for a given tenant.

SECURITY RULES:
  - Never log plain-text secret values.
  - All secrets must be encrypted with Fernet before DB storage.
  - install_status = 'configuration_required' if any required secret slot is missing.
  - install_status = 'connection_failed' if connection test fails after config.
  - install_status = 'enabled' only after all required slots are filled AND tests pass.
  - Package checksum is verified before any DB changes.
  - Manifest is validated before install proceeds.

Install flow:
  1. Unzip package to temp dir
  2. Verify package_checksum.txt
  3. Validate manifest.json
  4. Check compatibility (DB version, platform, tenant limits)
  5. Register tool in tool_packages + tool_config_slots
  6. Collect slot values from caller-supplied dict
  7. Save config to tenant_tool_config / secrets to tenant_tool_secret
  8. Run connection tests (if connection_tests.json present)
  9. Set install_status accordingly
 10. Write audit log entry
"""

import hashlib
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tool_secret_store import save_secret, save_config, mask_secret
from tool_manifest_validator import validate_manifest_dict, verify_package_checksum


# ── Constants ─────────────────────────────────────────────────────────────────
INSTALL_STATUS_ENABLED               = "enabled"
INSTALL_STATUS_CONFIG_REQUIRED       = "configuration_required"
INSTALL_STATUS_CONNECTION_FAILED     = "connection_failed"
INSTALL_STATUS_INSTALLED             = "installed"
INSTALL_STATUS_CONFIGURED            = "configured"

REQUIRED_PACKAGE_FILES = [
    "manifest.json",
    "install.py",
    "uninstall.py",
    "config_schema.json",
]

OPTIONAL_PACKAGE_FILES = [
    "connection_tests.json",
    "commands.json",
    "permissions.json",
    "pricing.json",
    "hub_tiles.json",
    "README.md",
]


# ── Exceptions ────────────────────────────────────────────────────────────────
class InstallError(Exception):
    """Raised when installation cannot proceed."""


class ChecksumError(InstallError):
    """Package checksum verification failed."""


class ManifestError(InstallError):
    """Manifest validation failed."""


class CompatibilityError(InstallError):
    """Tool is not compatible with this environment."""


# ── Package extraction ────────────────────────────────────────────────────────

def _extract_zip(zip_path: str, dest_dir: str) -> Path:
    """Extract .zip to dest_dir. Returns path to extracted dir."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Security: reject paths with directory traversal
        for member in zf.namelist():
            if ".." in member or member.startswith("/"):
                raise InstallError(f"Unsafe path in package: '{member}'")
        zf.extractall(dest)
    return dest


def _read_json_from_dir(pkg_dir: Path, filename: str) -> dict | None:
    """Read and parse a JSON file from the package dir. Returns None if missing."""
    path = pkg_dir / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text_from_dir(pkg_dir: Path, filename: str) -> str | None:
    """Read a text file from the package dir. Returns None if missing."""
    path = pkg_dir / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


# ── Checksum verification ─────────────────────────────────────────────────────

def _verify_checksum(pkg_dir: Path) -> str:
    """
    Read expected checksum from package_checksum.txt and verify.
    Returns the verified checksum hex string.
    Raises ChecksumError on mismatch or missing file.
    """
    checksum_file = pkg_dir / "package_checksum.txt"
    if not checksum_file.exists():
        raise ChecksumError("package_checksum.txt missing from package")

    expected = checksum_file.read_text(encoding="utf-8").strip()

    # Compute actual (excluding the checksum file itself)
    hasher = hashlib.sha256()
    for path in sorted(pkg_dir.rglob("*")):
        if path.is_file() and path.name != "package_checksum.txt":
            hasher.update(path.name.encode())
            hasher.update(path.read_bytes())
    actual = hasher.hexdigest()

    if actual != expected:
        raise ChecksumError(
            f"Package checksum mismatch.\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {actual}\n"
            f"Package may be corrupted or tampered with."
        )
    return expected


# ── DB registration ───────────────────────────────────────────────────────────

def _upsert_tool_package(conn, manifest: dict, config_schema: dict,
                          pkg_dir: Path, checksum: str, installed_by: int | None):
    """Insert or update the tool_packages record."""
    tool_id   = manifest["tool_id"]
    version   = manifest.get("version", "1.0.0")
    pkg_path  = str(pkg_dir)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO crm.tool_packages
                (tool_id, version, manifest_json, config_schema_json,
                 package_path, checksum, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (tool_id, version)
            DO UPDATE SET
                manifest_json     = EXCLUDED.manifest_json,
                config_schema_json = EXCLUDED.config_schema_json,
                package_path      = EXCLUDED.package_path,
                checksum          = EXCLUDED.checksum,
                created_by        = EXCLUDED.created_by,
                created_at        = NOW()
        """, (
            tool_id, version,
            json.dumps(manifest), json.dumps(config_schema),
            pkg_path, checksum, installed_by,
        ))


def _upsert_config_slots(conn, tool_id: str, config_schema: dict):
    """Register slot definitions in tool_config_slots."""
    slots = config_schema.get("slots", [])
    for slot in slots:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm.tool_config_slots
                    (tool_id, slot_name, display_name, description,
                     required, secret, replaceable,
                     validation_type, default_value, scope, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_tool_slot
                DO UPDATE SET
                    display_name    = EXCLUDED.display_name,
                    description     = EXCLUDED.description,
                    required        = EXCLUDED.required,
                    secret          = EXCLUDED.secret,
                    replaceable     = EXCLUDED.replaceable,
                    validation_type = EXCLUDED.validation_type,
                    default_value   = EXCLUDED.default_value,
                    scope           = EXCLUDED.scope,
                    sort_order      = EXCLUDED.sort_order
            """, (
                tool_id,
                slot.get("slot_name"),
                slot.get("display_name") or slot.get("slot_name"),
                slot.get("description") or "",
                bool(slot.get("required", True)),
                bool(slot.get("secret", False)),
                bool(slot.get("replaceable", True)),
                slot.get("validation_type") or "text",
                slot.get("default_value"),
                slot.get("scope") or "tenant",
                slot.get("sort_order"),
            ))


def _upsert_connection_tests(conn, tool_id: str, tests: list[dict]):
    """Register connection test definitions in tool_connection_tests."""
    for i, test in enumerate(tests):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm.tool_connection_tests
                    (tool_id, test_name, test_type, test_config_json,
                     required_slots_json, failure_message, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT uq_tool_connection_test
                DO UPDATE SET
                    test_type          = EXCLUDED.test_type,
                    test_config_json   = EXCLUDED.test_config_json,
                    required_slots_json = EXCLUDED.required_slots_json,
                    failure_message    = EXCLUDED.failure_message,
                    sort_order         = EXCLUDED.sort_order
            """, (
                tool_id,
                test.get("test_name", f"test_{i+1}"),
                test.get("test_type", "http_get"),
                json.dumps(test.get("test_config_json") or test.get("config", {})),
                json.dumps(test.get("required_slots_json") or test.get("required_slots", [])),
                test.get("failure_message") or "Connection test failed",
                test.get("sort_order", i + 1),
            ))


def _register_tool_registry(conn, tool_id: str, tenant_id: int,
                              manifest: dict, install_status: str,
                              installed_by: int | None):
    """Create or update tool_registry row for this tenant."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO crm.tool_registry
                (tool_id, tenant_id, tool_name, description, version, author,
                 risk_level, entry_point, install_script, uninstall_script,
                 license_type, supported_platforms,
                 install_status, installed_by, installed_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT ON CONSTRAINT uq_tool_registry
            DO UPDATE SET
                tool_name         = EXCLUDED.tool_name,
                description       = EXCLUDED.description,
                version           = EXCLUDED.version,
                author            = EXCLUDED.author,
                risk_level        = EXCLUDED.risk_level,
                entry_point       = EXCLUDED.entry_point,
                install_script    = EXCLUDED.install_script,
                uninstall_script  = EXCLUDED.uninstall_script,
                license_type      = EXCLUDED.license_type,
                supported_platforms = EXCLUDED.supported_platforms,
                install_status    = EXCLUDED.install_status,
                installed_by      = EXCLUDED.installed_by,
                updated_at        = NOW()
        """, (
            tool_id, tenant_id,
            manifest.get("tool_name") or tool_id,
            manifest.get("description") or "",
            manifest.get("version") or "1.0.0",
            manifest.get("author") or "unknown",
            manifest.get("risk_level") or "medium",
            manifest.get("entry_point") or "install.py",
            manifest.get("install_script") or "install.py",
            manifest.get("uninstall_script") or "uninstall.py",
            manifest.get("license_type") or "proprietary",
            json.dumps(manifest.get("supported_platforms") or ["all"]),
            install_status,
            installed_by,
        ))


# ── Slot value writer ─────────────────────────────────────────────────────────

def _write_slot_values(
    conn,
    tool_id: str,
    tenant_id: int,
    config_schema: dict,
    slot_values: dict[str, str],
    installed_by: int | None,
) -> tuple[list[str], list[str]]:
    """
    Write provided slot values to tenant_tool_config or tenant_tool_secret.
    Returns (filled_slots, missing_required_slots).
    """
    filled: list[str]  = []
    missing: list[str] = []
    slots = config_schema.get("slots", [])

    for slot in slots:
        sname    = slot.get("slot_name", "")
        is_secret = bool(slot.get("secret", False))
        required  = bool(slot.get("required", True))
        value     = slot_values.get(sname)

        # Fall back to default_value for non-secret config slots
        if value is None and not is_secret:
            value = slot.get("default_value")

        # Handle encrypted_secrets export: decrypt export_encrypted_value if present
        if value is None and is_secret:
            manifest_slot = _find_manifest_slot(
                slot_values.get("__manifest__"), sname
            )
            if manifest_slot and manifest_slot.get("export_encrypted_value"):
                from tool_secret_store import decrypt_secret as _dec
                export_key_ref = manifest_slot.get("export_key_ref", "export_v1")
                try:
                    value = _dec(manifest_slot["export_encrypted_value"], export_key_ref)
                except Exception:
                    value = None  # decryption failed; treat as missing

        if value is not None:
            if is_secret:
                save_secret(conn, tenant_id, tool_id, sname, value, updated_by=installed_by)
            else:
                save_config(conn, tenant_id, tool_id, sname,
                            value_text=value, updated_by=installed_by)
            filled.append(sname)
        elif required:
            missing.append(sname)

    return filled, missing


def _find_manifest_slot(manifest: dict | None, slot_name: str) -> dict | None:
    """Find a slot definition in manifest required_secret_slots by name."""
    if not manifest:
        return None
    for slot in manifest.get("required_secret_slots", []):
        if slot.get("slot_name") == slot_name:
            return slot
    return None


# ── Connection test runner (inline to avoid circular imports) ─────────────────

def _run_connection_tests(conn, tool_id: str, tenant_id: int,
                           override_slot_values: dict | None = None) -> dict:
    """Run connection tests. Returns {all_passed, tests}."""
    try:
        from tool_connection_test import run_tool_connection_tests
        return run_tool_connection_tests(tool_id, tenant_id, conn, override_slot_values)
    except Exception as exc:
        return {
            "all_passed": False,
            "tests": [{"test_name": "import_error", "passed": False,
                        "detail": str(exc), "status_code": None}],
        }


# ── Audit log ─────────────────────────────────────────────────────────────────

def _write_install_audit(
    conn, tenant_id: int, tool_id: str, version: str,
    install_status: str, installed_by: int | None,
    filled_slots: list[str], missing_slots: list[str],
    test_results: dict | None, error: str | None,
):
    """Write install audit entry to tool_export_logs (reused as general audit)."""
    try:
        import json as _json
        details = {
            "slots_filled": filled_slots,
            "missing_slots": missing_slots,
            "tests": "pass" if test_results and test_results.get("all_passed") else "fail/skipped",
        }
        if error:
            details["error"] = error
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm.tool_export_logs
                    (tenant_id, tool_id, export_mode, export_status,
                     export_details_json, secrets_exported, exported_by, exported_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, NOW())
            """, (
                tenant_id, tool_id,
                f"install:{install_status}",
                "success" if not error else "failed",
                _json.dumps(details),
                False,
                installed_by,
            ))
    except Exception:
        pass  # best-effort — install still succeeds


# ── Compatibility check ───────────────────────────────────────────────────────

def _check_compatibility(manifest: dict, tenant_id: int):
    """
    Basic compatibility check.
    Extend this as needed (DB version check, subscription tier, etc.).
    Raises CompatibilityError if tool cannot be installed.
    """
    platforms = manifest.get("supported_platforms") or ["all"]
    if "all" not in platforms and "windows" not in platforms and "linux" not in platforms:
        raise CompatibilityError(
            f"Tool '{manifest['tool_id']}' does not support this platform. "
            f"Supported: {platforms}"
        )
    # Risk level gate: 'critical' tools require explicit admin override in future
    # For now: log warning only, do not block.


# ── Main entry point ──────────────────────────────────────────────────────────

def install_tool(
    conn,
    zip_path: str,
    tenant_id: int,
    slot_values: dict[str, str] | None = None,
    installed_by: int | None = None,
    skip_connection_test: bool = False,
    force_reinstall: bool = False,
) -> dict:
    """
    Install a tool package from a .zip file.

    conn                  – psycopg2 connection (autocommit=False)
    zip_path              – absolute path to the .zip package
    tenant_id             – target tenant
    slot_values           – {slot_name: plain_value} for config/secret slots
    installed_by          – user_id for audit log
    skip_connection_test  – skip running connection_tests.json after install
    force_reinstall       – allow re-installing an already-enabled tool

    Returns:
        {
          "tool_id":          str,
          "version":          str,
          "install_status":   str,
          "filled_slots":     list[str],
          "missing_slots":    list[str],
          "test_results":     dict | None,
          "warnings":         list[str],
          "error":            str | None,
        }
    """
    slot_values = dict(slot_values or {})
    warnings: list[str] = []
    error: str | None = None

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Extract
        try:
            pkg_dir = _extract_zip(zip_path, tmpdir)
        except Exception as exc:
            raise InstallError(f"Failed to extract package: {exc}") from exc

        # 2. Check required files
        for fname in REQUIRED_PACKAGE_FILES:
            if not (pkg_dir / fname).exists():
                raise InstallError(f"Required package file missing: {fname}")
        for fname in OPTIONAL_PACKAGE_FILES:
            if not (pkg_dir / fname).exists():
                warnings.append(f"Optional file missing: {fname}")

        # 3. Verify checksum
        checksum = _verify_checksum(pkg_dir)

        # 4. Load manifest + config_schema
        manifest     = _read_json_from_dir(pkg_dir, "manifest.json")
        config_schema = _read_json_from_dir(pkg_dir, "config_schema.json") or {"slots": []}
        conn_tests    = _read_json_from_dir(pkg_dir, "connection_tests.json")
        permissions   = _read_json_from_dir(pkg_dir, "permissions.json")
        commands      = _read_json_from_dir(pkg_dir, "commands.json")
        pricing       = _read_json_from_dir(pkg_dir, "pricing.json")

        if not manifest:
            raise InstallError("manifest.json is empty or not valid JSON")

        tool_id = manifest.get("tool_id", "")
        version = manifest.get("version", "1.0.0")

        # 5. Validate manifest
        validation = validate_manifest_dict(manifest, config_schema)
        if not validation["valid"]:
            raise ManifestError(
                "Manifest validation failed:\n" +
                "\n".join(f"  - {e}" for e in validation["errors"])
            )
        warnings.extend([f"Manifest warning: {w}" for w in validation.get("warnings", [])])

        # 6. Compatibility check
        _check_compatibility(manifest, tenant_id)

        # 7. Check for existing install (if not force_reinstall)
        if not force_reinstall:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT install_status FROM crm.tool_registry
                     WHERE tool_id = %s AND tenant_id = %s
                """, (tool_id, tenant_id))
                row = cur.fetchone()
            if row and row["install_status"] == INSTALL_STATUS_ENABLED:
                raise InstallError(
                    f"Tool '{tool_id}' is already enabled for tenant {tenant_id}. "
                    f"Use force_reinstall=True to overwrite."
                )

        # 8. Register package + slots in DB
        _upsert_tool_package(conn, manifest, config_schema, pkg_dir, checksum, installed_by)
        _upsert_config_slots(conn, tool_id, config_schema)
        if conn_tests and isinstance(conn_tests, list):
            _upsert_connection_tests(conn, tool_id, conn_tests)
        elif conn_tests and isinstance(conn_tests, dict) and "tests" in conn_tests:
            _upsert_connection_tests(conn, tool_id, conn_tests["tests"])

        # 9a. Register hub tiles if provided
        hub_tiles = _read_json_from_dir(pkg_dir, "hub_tiles.json")
        if hub_tiles and isinstance(hub_tiles, list):
            _upsert_hub_tiles(conn, tool_id, tenant_id, hub_tiles)
        elif hub_tiles and isinstance(hub_tiles, dict) and "tiles" in hub_tiles:
            _upsert_hub_tiles(conn, tool_id, tenant_id, hub_tiles["tiles"])

        # 9. Store the manifest reference in slot_values so _write_slot_values
        #    can read export_encrypted_value from secret slots if present.
        slot_values["__manifest__"] = manifest

        # 10. Write slot values
        filled_slots, missing_slots = _write_slot_values(
            conn, tool_id, tenant_id, config_schema, slot_values, installed_by
        )
        slot_values.pop("__manifest__", None)

        # 11. Determine initial status
        if missing_slots:
            # Has required slots that were not provided
            install_status = INSTALL_STATUS_CONFIG_REQUIRED
        else:
            install_status = INSTALL_STATUS_CONFIGURED

        # 12. Run connection tests (if slots are filled and tests defined)
        test_results: dict | None = None
        if install_status == INSTALL_STATUS_CONFIGURED and not skip_connection_test:
            test_results = _run_connection_tests(conn, tool_id, tenant_id)
            if test_results.get("all_passed"):
                install_status = INSTALL_STATUS_ENABLED
            else:
                install_status = INSTALL_STATUS_CONNECTION_FAILED
                error = "One or more connection tests failed"
        elif install_status == INSTALL_STATUS_CONFIG_REQUIRED:
            warnings.append(
                f"Missing required slots: {missing_slots}. "
                f"Fill them via PUT /tools/{tool_id}/config/{{slot_name}} "
                f"then run POST /tools/{tool_id}/test-connection to enable."
            )

        # 13. Register in tool_registry
        _register_tool_registry(
            conn, tool_id, tenant_id, manifest, install_status, installed_by
        )

        # 14. Audit log
        _write_install_audit(
            conn, tenant_id, tool_id, version, install_status,
            installed_by, filled_slots, missing_slots, test_results, error
        )

        return {
            "tool_id":        tool_id,
            "version":        version,
            "install_status": install_status,
            "filled_slots":   filled_slots,
            "missing_slots":  missing_slots,
            "test_results":   test_results,
            "warnings":       warnings,
            "error":          error,
        }



def _upsert_hub_tiles(conn, tool_id: str, tenant_id: int, tiles: list):
    """Insert/update tool hub tiles for a tenant when a plugin is installed."""
    if not tiles:
        return
    with conn.cursor() as cur:
        for tile in tiles:
            cur.execute("""
                INSERT INTO crm.tool_hub_tiles
                    (tenant_id, tile_key, tile_title_en, tile_title_cs, tile_title_pl,
                     tile_hint_en, tile_hint_cs, tile_hint_pl,
                     tool_id, icon, sort_order, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (tenant_id, tile_key) DO UPDATE SET
                    tile_title_en = EXCLUDED.tile_title_en,
                    tile_title_cs = EXCLUDED.tile_title_cs,
                    tile_title_pl = EXCLUDED.tile_title_pl,
                    tile_hint_en  = EXCLUDED.tile_hint_en,
                    tile_hint_cs  = EXCLUDED.tile_hint_cs,
                    tile_hint_pl  = EXCLUDED.tile_hint_pl,
                    tool_id       = EXCLUDED.tool_id,
                    icon          = EXCLUDED.icon,
                    sort_order    = EXCLUDED.sort_order,
                    is_active     = TRUE
            """, (
                tenant_id,
                tile.get("tile_key"),
                tile.get("tile_title_en", ""),
                tile.get("tile_title_cs"),
                tile.get("tile_title_pl"),
                tile.get("tile_hint_en"),
                tile.get("tile_hint_cs"),
                tile.get("tile_hint_pl"),
                tool_id,
                tile.get("icon", "Extension"),
                tile.get("sort_order", 0),
            ))


def _remove_hub_tiles(conn, tool_id: str, tenant_id: int):
    """Deactivate hub tiles registered by a specific tool on uninstall."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE crm.tool_hub_tiles
               SET is_active = FALSE
             WHERE tenant_id = %s AND tool_id = %s
        """, (tenant_id, tool_id))


# ── Slot update (called from PUT /tools/{tool_id}/config/{slot_name}) ─────────

def update_tool_slot(
    conn,
    tool_id: str,
    tenant_id: int,
    slot_name: str,
    plain_value: str,
    updated_by: int | None = None,
    run_connection_test: bool = True,
) -> dict:
    """
    Update a single config or secret slot for an installed tool.
    Optionally re-runs connection tests and updates install_status.

    Returns:
        {"tool_id", "slot_name", "install_status", "test_results", "masked_value"}
    """
    # Look up slot definition
    with conn.cursor() as cur:
        cur.execute("""
            SELECT slot_name, secret, required, replaceable
              FROM crm.tool_config_slots
             WHERE tool_id = %s AND slot_name = %s
        """, (tool_id, slot_name))
        slot = cur.fetchone()

    if not slot:
        raise InstallError(f"Unknown slot '{slot_name}' for tool '{tool_id}'")

    if not slot["replaceable"]:
        raise InstallError(f"Slot '{slot_name}' is not replaceable")

    # Write value
    if slot["secret"]:
        save_secret(conn, tenant_id, tool_id, slot_name, plain_value, updated_by=updated_by)
    else:
        save_config(conn, tenant_id, tool_id, slot_name,
                    value_text=plain_value, updated_by=updated_by)

    # Re-check if all required slots are now filled
    with conn.cursor() as cur:
        cur.execute("""
            SELECT slot_name, secret, required
              FROM crm.tool_config_slots
             WHERE tool_id = %s AND required = TRUE
        """, (tool_id,))
        required_slots = [dict(r) for r in cur.fetchall()]

    all_filled = True
    for rs in required_slots:
        sname = rs["slot_name"]
        is_secret = rs["secret"]
        if is_secret:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM crm.tenant_tool_secret
                     WHERE tenant_id=%s AND tool_id=%s AND slot_name=%s AND is_active=TRUE
                """, (tenant_id, tool_id, sname))
                if not cur.fetchone():
                    all_filled = False
                    break
        else:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT value_text FROM crm.tenant_tool_config
                     WHERE tenant_id=%s AND tool_id=%s AND slot_name=%s AND is_active=TRUE
                """, (tenant_id, tool_id, sname))
                row = cur.fetchone()
                if not row or not row["value_text"]:
                    all_filled = False
                    break

    test_results: dict | None = None
    if all_filled and run_connection_test:
        test_results = _run_connection_tests(conn, tool_id, tenant_id)
        new_status = (
            INSTALL_STATUS_ENABLED if test_results.get("all_passed")
            else INSTALL_STATUS_CONNECTION_FAILED
        )
    elif all_filled:
        new_status = INSTALL_STATUS_CONFIGURED
    else:
        new_status = INSTALL_STATUS_CONFIG_REQUIRED

    with conn.cursor() as cur:
        cur.execute("""
            UPDATE crm.tool_registry
               SET install_status = %s, updated_at = NOW()
             WHERE tool_id = %s AND tenant_id = %s
        """, (new_status, tool_id, tenant_id))

    return {
        "tool_id":        tool_id,
        "slot_name":      slot_name,
        "install_status": new_status,
        "test_results":   test_results,
        "masked_value":   mask_secret(plain_value),
    }


# ── Uninstall ─────────────────────────────────────────────────────────────────

def uninstall_tool(
    conn,
    tool_id: str,
    tenant_id: int,
    purge_secrets: bool = False,
    uninstalled_by: int | None = None,
) -> dict:
    """
    Deactivate a tool for a tenant.
    purge_secrets=True also deactivates tenant_tool_secret rows.
    Requires destructive confirmation from the caller (never called automatically).

    Returns: {"tool_id", "status", "secrets_purged"}
    """
    with conn.cursor() as cur:
        # Deactivate config
        cur.execute("""
            UPDATE crm.tenant_tool_config
               SET is_active = FALSE, updated_at = NOW()
             WHERE tenant_id = %s AND tool_id = %s
        """, (tenant_id, tool_id))

        if purge_secrets:
            cur.execute("""
                UPDATE crm.tenant_tool_secret
                   SET is_active = FALSE, updated_at = NOW()
                 WHERE tenant_id = %s AND tool_id = %s
            """, (tenant_id, tool_id))

        # Deactivate hub tiles registered by this tool
        _remove_hub_tiles(conn, tool_id, tenant_id)

        cur.execute("""
            UPDATE crm.tool_registry
               SET install_status = 'uninstalled', updated_at = NOW()
             WHERE tenant_id = %s AND tool_id = %s
        """, (tenant_id, tool_id))

    _write_install_audit(
        conn, tenant_id, tool_id, "", "uninstalled",
        uninstalled_by, [], [], None,
        error=f"purge_secrets={purge_secrets}"
    )

    return {
        "tool_id":        tool_id,
        "status":         "uninstalled",
        "secrets_purged": purge_secrets,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import psycopg2
    import psycopg2.extras

    parser = argparse.ArgumentParser(description="Install a tool package")
    parser.add_argument("zip_path",               help="Path to tool package .zip")
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--db-url",    default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--skip-test", action="store_true")
    parser.add_argument("--force",     action="store_true")
    args = parser.parse_args()

    if not args.db_url:
        print("Error: --db-url or DATABASE_URL env required", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(args.db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    try:
        result = install_tool(
            conn,
            zip_path=args.zip_path,
            tenant_id=args.tenant_id,
            skip_connection_test=args.skip_test,
            force_reinstall=args.force,
        )
        conn.commit()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["install_status"] in (INSTALL_STATUS_ENABLED, INSTALL_STATUS_CONFIG_REQUIRED) else 1)
    except (InstallError, ManifestError, ChecksumError, CompatibilityError) as e:
        conn.rollback()
        print(f"Install failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        conn.rollback()
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()
