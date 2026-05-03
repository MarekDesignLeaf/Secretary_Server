"""
tool_connection_test.py
Runs connection tests for a tool using its connection_tests.json definition.
Secrets are injected at runtime and NEVER logged in plain text.
"""

import json
import re
import urllib.request
import urllib.error
from typing import Any

from tool_secret_store import decrypt_secret, mask_secret   # local module (see below)


# ── Masking helpers ───────────────────────────────────────────────────────────
def _mask(value: str) -> str:
    """Show first 8 chars + asterisks. e.g. sk_live_1234 → sk_live_1********"""
    return mask_secret(value)


def _inject_slots(template: str, slot_values: dict[str, str]) -> str:
    """
    Replace {slot_name} placeholders in a template string with actual values.
    Used for endpoint URLs, headers, and request bodies.
    """
    result = template
    for slot_name, value in slot_values.items():
        result = result.replace(f"{{{slot_name}}}", value)
    return result


def _inject_slots_dict(obj: Any, slot_values: dict[str, str]) -> Any:
    """Recursively inject slot values into a dict/list/str structure."""
    if isinstance(obj, str):
        return _inject_slots(obj, slot_values)
    if isinstance(obj, dict):
        return {k: _inject_slots_dict(v, slot_values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_inject_slots_dict(item, slot_values) for item in obj]
    return obj


# ── Single test execution ─────────────────────────────────────────────────────
def run_single_test(
    test_def: dict,
    slot_values: dict[str, str],
    timeout_ms: int = 10000,
) -> dict:
    """
    Execute one connection test step.

    test_def format (from connection_tests.json):
        test_name        str
        test_type        "http_get" | "http_post" | "custom"
        test_config_json {method, endpoint, headers, body_template,
                          expected_status, success_json_path, timeout_ms}
        required_slots_json  ["api_key", ...]
        failure_message  str

    Returns:
        {test_name, passed, status_code, detail, masked_slots_used}
    """
    test_name     = test_def.get("test_name", "unnamed")
    test_type     = test_def.get("test_type", "http_get")
    cfg           = test_def.get("test_config_json", {})
    required_slots = test_def.get("required_slots_json", [])
    failure_msg   = test_def.get("failure_message", "Connection test failed")

    # Verify all required slots are present
    missing = [s for s in required_slots if s not in slot_values or not slot_values[s]]
    if missing:
        return {
            "test_name":        test_name,
            "passed":           False,
            "status_code":      None,
            "detail":           f"Missing required slots: {missing}",
            "masked_slots_used": {},
        }

    # Build masked log (never log plain values)
    masked_slots = {k: _mask(v) for k, v in slot_values.items() if k in required_slots}

    try:
        if test_type in ("http_get", "http_post", "http"):
            result = _run_http_test(cfg, slot_values, timeout_ms)
        elif test_type == "oauth_token":
            result = _run_oauth_test(cfg, slot_values, timeout_ms)
        else:
            return {
                "test_name":        test_name,
                "passed":           False,
                "status_code":      None,
                "detail":           f"Unknown test_type: {test_type}",
                "masked_slots_used": masked_slots,
            }

        return {
            "test_name":        test_name,
            "passed":           result["passed"],
            "status_code":      result.get("status_code"),
            "detail":           result.get("detail", failure_msg if not result["passed"] else "OK"),
            "masked_slots_used": masked_slots,
        }

    except Exception as exc:
        return {
            "test_name":        test_name,
            "passed":           False,
            "status_code":      None,
            "detail":           f"Exception: {type(exc).__name__}: {exc}",
            "masked_slots_used": masked_slots,
        }


def _run_http_test(cfg: dict, slot_values: dict, timeout_ms: int) -> dict:
    method          = cfg.get("method", "GET").upper()
    endpoint        = _inject_slots(cfg.get("endpoint", ""), slot_values)
    headers_tpl     = cfg.get("headers", {})
    body_tpl        = cfg.get("body_template", "")
    expected_status = cfg.get("expected_status", 200)
    success_path    = cfg.get("success_json_path")   # e.g. "data.id" to check response body
    timeout_s       = min(cfg.get("timeout_ms", timeout_ms), 30000) / 1000

    headers = {k: _inject_slots(v, slot_values) for k, v in headers_tpl.items()}
    body    = _inject_slots(body_tpl, slot_values).encode() if body_tpl else None

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            raw    = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        raw    = e.read().decode(errors="replace") if e.fp else ""

    passed = (status == expected_status)

    # Optional: verify a JSON path exists in the response
    detail = f"HTTP {status}"
    if passed and success_path:
        try:
            data = json.loads(raw)
            keys = success_path.split(".")
            node = data
            for k in keys:
                node = node[k]
            detail += f" | {success_path}={str(node)[:40]}"
        except Exception:
            passed = False
            detail += f" | success_json_path '{success_path}' not found in response"

    return {"passed": passed, "status_code": status, "detail": detail}


def _run_oauth_test(cfg: dict, slot_values: dict, timeout_ms: int) -> dict:
    """
    Test OAuth2 client_credentials flow.
    Expected cfg keys: token_endpoint, client_id_slot, client_secret_slot, scope
    """
    endpoint      = _inject_slots(cfg.get("token_endpoint", ""), slot_values)
    client_id     = slot_values.get(cfg.get("client_id_slot", "client_id"), "")
    client_secret = slot_values.get(cfg.get("client_secret_slot", "client_secret"), "")
    scope         = cfg.get("scope", "")
    timeout_s     = min(cfg.get("timeout_ms", timeout_ms), 30000) / 1000

    body = f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}"
    if scope:
        body += f"&scope={scope}"

    req = urllib.request.Request(
        endpoint,
        data=body.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode())
            if "access_token" in data:
                return {"passed": True, "status_code": 200, "detail": "OAuth token received"}
            return {"passed": False, "status_code": 200, "detail": "No access_token in response"}
    except urllib.error.HTTPError as e:
        return {"passed": False, "status_code": e.code, "detail": f"OAuth error HTTP {e.code}"}
    except Exception as exc:
        return {"passed": False, "status_code": None, "detail": str(exc)}


# ── Multi-step test runner ────────────────────────────────────────────────────
def run_tool_connection_tests(
    tool_id: str,
    tenant_id: int,
    conn,                       # psycopg2 connection
    override_slot_values: dict | None = None,
) -> dict:
    """
    Load all connection test definitions for tool_id from DB,
    decrypt required secrets, run each test.

    override_slot_values: optional dict of slot_name→plaintext to use instead of DB
    (used during install before secrets are written to DB).

    Returns:
        {
            "tool_id": ..., "tenant_id": ...,
            "all_passed": bool,
            "tests": [...per-test results...],
        }
    """
    # Load test definitions
    with conn.cursor() as cur:
        cur.execute("""
            SELECT test_name, test_type, test_config_json, required_slots_json,
                   failure_message, sort_order
              FROM crm.tool_connection_tests
             WHERE tool_id = %s
             ORDER BY sort_order
        """, (tool_id,))
        test_defs = [dict(r) for r in cur.fetchall()]

    if not test_defs:
        return {
            "tool_id": tool_id, "tenant_id": tenant_id,
            "all_passed": True, "tests": [],
            "note": "No connection tests defined for this tool",
        }

    # Collect all required slot names across all tests
    all_required = set()
    for td in test_defs:
        for s in (td.get("required_slots_json") or []):
            all_required.add(s)

    # Load slot values: override first, then DB config, then DB secrets
    slot_values: dict[str, str] = dict(override_slot_values or {})
    missing_from_override = all_required - set(slot_values.keys())

    if missing_from_override:
        # Load plain config
        with conn.cursor() as cur:
            cur.execute("""
                SELECT slot_name, value_text
                  FROM crm.tenant_tool_config
                 WHERE tenant_id = %s AND tool_id = %s
                   AND slot_name = ANY(%s) AND is_active = TRUE
            """, (tenant_id, tool_id, list(missing_from_override)))
            for row in cur.fetchall():
                slot_values[row["slot_name"]] = row["value_text"] or ""

        # Load secrets (decrypt)
        still_missing = missing_from_override - set(slot_values.keys())
        if still_missing:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT slot_name, encrypted_value, encryption_key_ref
                      FROM crm.tenant_tool_secret
                     WHERE tenant_id = %s AND tool_id = %s
                       AND slot_name = ANY(%s) AND is_active = TRUE
                """, (tenant_id, tool_id, list(still_missing)))
                for row in cur.fetchall():
                    try:
                        slot_values[row["slot_name"]] = decrypt_secret(
                            row["encrypted_value"], row["encryption_key_ref"]
                        )
                    except Exception as e:
                        slot_values[row["slot_name"]] = ""  # decryption failed

    # Run tests
    results = []
    for td in test_defs:
        r = run_single_test(td, slot_values)
        results.append(r)

    all_passed = all(r["passed"] for r in results)

    # Update session state in DB (tool_registry install_status) if applicable
    _update_tool_status_after_test(conn, tool_id, tenant_id, all_passed)

    return {
        "tool_id":    tool_id,
        "tenant_id":  tenant_id,
        "all_passed": all_passed,
        "tests":      results,
    }


def _update_tool_status_after_test(conn, tool_id: str, tenant_id: int, passed: bool):
    """Update tool_registry.install_status after a connection test (best-effort)."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE crm.tool_registry
                   SET install_status = %s, updated_at = NOW()
                 WHERE tool_id = %s AND tenant_id = %s
                   AND install_status IN ('configured','connection_failed','enabled')
            """, (
                "enabled" if passed else "connection_failed",
                tool_id,
                tenant_id,
            ))
    except Exception:
        pass  # tool_registry may not exist yet during install
