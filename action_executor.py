"""
action_executor.py — Secretary CRM Unified Action Executor
===========================================================
Every UI button, voice command and API call that modifies data
MUST go through execute_action(). This ensures:

  - Consistent permission checks
  - Consistent risk-level confirmation requirements
  - Full audit logging in voice_command_logs
  - Single source of truth for what each action does

Usage
-----
    from action_executor import execute_action, register_handler, ActionResult

    result = await execute_action(
        action_code  = "task.create",
        args         = {"title": "Prune roses", "client_id": 42},
        conn         = db_conn,
        tenant_id    = 1,
        user_id      = 7,
        source       = "voice",        # "voice" | "manual" | "api" | "automation"
        session_id   = "vs_abc123",    # voice session id, or None
        lang         = "cs",
    )
    # result.ok, result.reply, result.action_type, result.data

Registering a new handler
-------------------------
    @register_handler("contact.create")
    def handle_contact_create(args, conn, tenant_id, user_id, lang, **kw):
        ...
        return ActionResult(ok=True, reply="Kontakt vytvořen.", action_type="REFRESH")
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

# ---------------------------------------------------------------------------
# ActionResult
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    ok: bool
    reply: str = ""
    action_type: str = ""          # REFRESH, NAVIGATE, OPEN_PHONE, OPEN_MAPS, ...
    action_data: Dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_HANDLERS: Dict[str, Callable] = {}


def register_handler(action_code: str):
    """Decorator — registers a function as the handler for action_code."""
    def decorator(fn: Callable) -> Callable:
        _HANDLERS[action_code] = fn
        return fn
    return decorator


def get_registered_actions() -> list[str]:
    return list(_HANDLERS.keys())


# ---------------------------------------------------------------------------
# DB helpers (inline to avoid circular imports)
# ---------------------------------------------------------------------------

def _get_action_def(conn, tenant_id: int, action_code: str) -> dict:
    """Fetch action definition from action_registry. Falls back to global (tenant_id=0)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT action_code, risk_level, requires_confirmation,
                          confirmation_phrase, requires_permission,
                          voice_enabled, api_enabled, manual_ui_enabled,
                          enabled
                     FROM crm.action_registry
                    WHERE action_code = %s
                      AND (tenant_id = %s OR tenant_id = 0)
                    ORDER BY tenant_id DESC
                    LIMIT 1""",
                (action_code, tenant_id),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    except Exception:
        return {}


def _check_permission(conn, tenant_id: int, user_id: Optional[int],
                      permission_code: Optional[str]) -> bool:
    """Return True if user has the required permission (or no permission needed)."""
    if not permission_code or user_id is None:
        return True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM crm.user_permissions
                    WHERE tenant_id = %s AND user_id = %s
                      AND permission_code = %s AND granted = TRUE
                    LIMIT 1""",
                (tenant_id, user_id, permission_code),
            )
            return cur.fetchone() is not None
    except Exception:
        return True   # fail open — permission table may not exist yet


def _log_command(conn, tenant_id: int, user_id: Optional[int],
                 session_id: Optional[str], action_code: str,
                 args: dict, status: str, result: Optional[ActionResult],
                 duration_ms: int, error: Optional[str]) -> Optional[int]:
    """Insert a row in voice_command_logs. Returns id or None."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO crm.voice_command_logs
                       (tenant_id, user_id, voice_session_id, raw_text,
                        detected_intent, action_code, slots_json,
                        execution_status, result_json, error_message, duration_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    tenant_id,
                    user_id,
                    session_id,
                    args.get("_raw_text", ""),
                    args.get("_intent_code"),
                    action_code,
                    json.dumps({k: v for k, v in args.items() if not k.startswith("_")}),
                    status,
                    json.dumps(result.action_data) if result else None,
                    error,
                    duration_ms,
                ),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _create_confirmation_request(conn, tenant_id: int, user_id: Optional[int],
                                  session_id: Optional[str], log_id: Optional[int],
                                  action_code: str, args: dict, risk_level: str,
                                  required_phrase: Optional[str]) -> str:
    """Create a pending confirmation request and return a token."""
    import uuid
    token = str(uuid.uuid4())
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO crm.voice_confirmation_requests
                       (tenant_id, user_id, voice_session_id, command_log_id,
                        action_code, slots_json, risk_level, required_phrase, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')""",
                (
                    tenant_id,
                    user_id,
                    session_id,
                    log_id,
                    action_code,
                    json.dumps({k: v for k, v in args.items() if not k.startswith("_")}),
                    risk_level,
                    required_phrase,
                ),
            )
        conn.commit()
    except Exception:
        pass
    return token


# ---------------------------------------------------------------------------
# execute_action — the single entry point
# ---------------------------------------------------------------------------

async def execute_action(
    action_code: str,
    args: dict,
    conn,
    tenant_id: int,
    user_id: Optional[int],
    source: str = "api",           # "voice" | "manual" | "api" | "automation"
    session_id: Optional[str] = None,
    lang: str = "en",
    confirmed: bool = False,       # caller sets True after user confirmed
    confirmation_token: Optional[str] = None,
) -> ActionResult:
    """
    Execute an action through the unified action system.

    Steps:
    1. Look up action_code in action_registry
    2. Check enabled + source permission (voice_enabled / api_enabled / manual_ui_enabled)
    3. Check user permission
    4. Check if confirmation required (and not yet confirmed)
    5. Dispatch to registered handler
    6. Log result in voice_command_logs
    7. Return ActionResult
    """
    t_start = time.monotonic()
    action_def = _get_action_def(conn, tenant_id, action_code)

    # ── 1. Action must be enabled ────────────────────────────────────────────
    if action_def and not action_def.get("enabled", True):
        result = ActionResult(
            ok=False,
            reply=_t("This action is disabled.", "Tato akce je zakázána.", "Ta akcja jest wyłączona.", lang),
            error="action_disabled",
        )
        _log_command(conn, tenant_id, user_id, session_id, action_code,
                     args, "rejected", result, 0, "action_disabled")
        return result

    # ── 2. Source permission check ───────────────────────────────────────────
    if action_def:
        source_key = {"voice": "voice_enabled", "manual": "manual_ui_enabled",
                      "api": "api_enabled", "automation": "automation_enabled"}.get(source, "api_enabled")
        if not action_def.get(source_key, True):
            result = ActionResult(
                ok=False,
                reply=_t("This action is not available via this channel.",
                         "Tato akce není dostupná přes tento kanál.",
                         "Ta akcja nie jest dostępna przez ten kanał.", lang),
                error="source_not_permitted",
            )
            _log_command(conn, tenant_id, user_id, session_id, action_code,
                         args, "rejected", result, 0, "source_not_permitted")
            return result

    # ── 3. User permission check ──────────────────────────────────────────────
    required_perm = action_def.get("requires_permission") if action_def else None
    if required_perm and not _check_permission(conn, tenant_id, user_id, required_perm):
        result = ActionResult(
            ok=False,
            reply=_t("You don't have permission for this action.",
                     "Na tuto akci nemáš oprávnění.",
                     "Nie masz uprawnień do tej akcji.", lang),
            error="permission_denied",
        )
        _log_command(conn, tenant_id, user_id, session_id, action_code,
                     args, "rejected", result, 0, "permission_denied")
        return result

    # ── 4. Confirmation check ────────────────────────────────────────────────
    risk = action_def.get("risk_level", "safe") if action_def else "safe"
    needs_confirm = action_def.get("requires_confirmation", False) if action_def else False

    if (needs_confirm or risk in ("sensitive", "destructive")) and not confirmed:
        required_phrase = action_def.get("confirmation_phrase") if action_def else None
        log_id = _log_command(conn, tenant_id, user_id, session_id, action_code,
                              args, "awaiting_confirmation", None, 0, None)
        token = _create_confirmation_request(
            conn, tenant_id, user_id, session_id, log_id,
            action_code, args, risk, required_phrase
        )
        if risk == "destructive" and required_phrase:
            reply = _t(
                f"This is a destructive action. Please say exactly: \"{required_phrase}\"",
                f"Toto je nevratná akce. Řekni přesně: \"{required_phrase}\"",
                f"To jest nieodwracalna akcja. Powiedz dokładnie: \"{required_phrase}\"",
                lang,
            )
        else:
            reply = _t(
                "Please confirm this action.",
                "Potvrď prosím tuto akci.",
                "Potwierdź proszę tę akcję.",
                lang,
            )
        result = ActionResult(
            ok=False,
            reply=reply,
            requires_confirmation=True,
            confirmation_token=token,
            error="awaiting_confirmation",
        )
        return result

    # ── 5. Dispatch to handler ───────────────────────────────────────────────
    handler = _HANDLERS.get(action_code)
    if not handler:
        result = ActionResult(
            ok=False,
            reply=_t(f"Action '{action_code}' is not implemented.",
                     f"Akce '{action_code}' není implementována.",
                     f"Akcja '{action_code}' nie jest zaimplementowana.", lang),
            error="handler_not_found",
        )
        _log_command(conn, tenant_id, user_id, session_id, action_code,
                     args, "failed", result, 0, "handler_not_found")
        return result

    try:
        result: ActionResult = handler(
            args=args,
            conn=conn,
            tenant_id=tenant_id,
            user_id=user_id,
            lang=lang,
            source=source,
            session_id=session_id,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"action_executor ERROR [{action_code}]: {exc}\n{tb}")
        result = ActionResult(ok=False, reply=str(exc), error=str(exc))

    duration_ms = int((time.monotonic() - t_start) * 1000)
    status = "executed" if result.ok else "failed"
    _log_command(conn, tenant_id, user_id, session_id, action_code,
                 args, status, result, duration_ms, result.error)

    return result


# ---------------------------------------------------------------------------
# Utility: minimal trilingual string helper
# ---------------------------------------------------------------------------

def _t(en: str, cs: str, pl: str, lang: str) -> str:
    code = (lang or "en").lower()[:2]
    return cs if code == "cs" else pl if code == "pl" else en


# ---------------------------------------------------------------------------
# Built-in handlers (reference implementations — extend in main.py)
# ---------------------------------------------------------------------------

@register_handler("system.ping")
def handle_ping(args, conn, tenant_id, user_id, lang, **kw):
    return ActionResult(ok=True, reply=_t("Pong.", "Pong.", "Pong.", lang), action_type="")


@register_handler("system.get_coverage")
def handle_get_coverage(args, conn, tenant_id, user_id, lang, **kw):
    """Return voice coverage summary from voice_coverage_map."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT coverage_status, count(*) as cnt
                     FROM crm.voice_coverage_map
                    WHERE tenant_id IN (%s, 0)
                    GROUP BY coverage_status
                    ORDER BY cnt DESC""",
                (tenant_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return ActionResult(ok=True, reply="Coverage fetched.", action_type="", action_data={"coverage": rows})
    except Exception as e:
        return ActionResult(ok=False, reply=str(e), error=str(e))
