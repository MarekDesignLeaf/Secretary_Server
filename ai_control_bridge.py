"""
ai_control_bridge.py — Secretary CRM Voice + UI Control Bridge
===============================================================
This module is the semantic bridge between what a user says
and the registered ui_control_registry + action_registry entries.

Architecture (from user's specification):
------------------------------------------
AI is NOT the decision maker. AI is a semantic bridge.

Resolution order for any voice command:
  1. Current screen context  (voice_context_state)
  2. Exact synonym match      (ui_control_synonyms)
  3. Entity alias match       (contacts / jobs / tasks name lookup)
  4. Semantic embedding match (ui_control_embeddings + pgvector)
  5. AI interpretation        (OpenAI → candidate list only)
  6. Clarification question   (if confidence low or multiple matches)

All paths end at:
  action_executor.execute_action(action_code, args, ...)

AI may suggest synonyms, but every suggestion goes to
ai_suggestion_review_queue (status='pending') first.
Admin approves/rejects before synonyms go active.

Public API
----------
  resolve_voice_command(text, conn, tenant_id, user_id, lang, session_id)
      → VoiceResolveResult

  update_voice_context(conn, tenant_id, user_id, screen_code,
                       module_name, entity_type, entity_id, extra)

  get_screen_controls(conn, tenant_id, screen_code, lang)
      → list[ControlSummary]

  generate_synonyms_for_control(conn, tenant_id, control_code,
                                 language_codes, openai_api_key, user_id)
      → job_id (UUID)

  approve_synonym(conn, tenant_id, suggestion_id, user_id)

  reject_synonym(conn, tenant_id, suggestion_id, user_id, note)
"""

from __future__ import annotations

import json
import uuid
import time
import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class VoiceResolveResult:
    """Result of resolving a voice command to a UI control / action."""
    resolved: bool = False
    control_code: Optional[str] = None
    action_code: Optional[str] = None
    confidence: float = 0.0
    resolution_method: str = ""   # exact_synonym | embedding | ai | clarification
    requires_clarification: bool = False
    clarification_question: str = ""
    candidates: list[dict] = field(default_factory=list)
    args: dict = field(default_factory=dict)
    risk_level: str = "safe"
    error: Optional[str] = None


@dataclass
class ControlSummary:
    """Lightweight summary of a UI control for voice help."""
    control_code: str
    label: str
    action_code: str
    risk_level: str
    voice_enabled: bool
    example_phrases: list[str] = field(default_factory=list)
    short_help: str = ""


# ---------------------------------------------------------------------------
# Context management
# ---------------------------------------------------------------------------

def update_voice_context(
    conn,
    tenant_id: int,
    user_id: int,
    screen_code: Optional[str] = None,
    module_name: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    session_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """
    Upsert voice_context_state for (tenant_id, user_id).
    Called whenever the Android app changes screens or selects an entity.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO crm.voice_context_state
                       (tenant_id, user_id, voice_session_id,
                        current_screen_code, current_module,
                        selected_entity_type, selected_entity_id,
                        context_json, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (tenant_id, user_id) DO UPDATE
                   SET voice_session_id     = EXCLUDED.voice_session_id,
                       current_screen_code  = COALESCE(EXCLUDED.current_screen_code, voice_context_state.current_screen_code),
                       current_module       = COALESCE(EXCLUDED.current_module,      voice_context_state.current_module),
                       selected_entity_type = EXCLUDED.selected_entity_type,
                       selected_entity_id   = EXCLUDED.selected_entity_id,
                       context_json         = COALESCE(EXCLUDED.context_json, '{}'),
                       updated_at           = now()""",
                (
                    tenant_id, user_id, session_id,
                    screen_code, module_name,
                    entity_type, entity_id,
                    json.dumps(extra or {}),
                ),
            )
        conn.commit()
    except Exception as e:
        print(f"[ai_control_bridge] update_voice_context error: {e}")


def _get_voice_context(conn, tenant_id: int, user_id: int) -> dict:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT current_screen_code, current_module,
                          selected_entity_type, selected_entity_id,
                          context_json, voice_session_id
                     FROM crm.voice_context_state
                    WHERE tenant_id = %s AND user_id = %s
                    LIMIT 1""",
                (tenant_id, user_id),
            )
            row = cur.fetchone()
            return dict(row) if row else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Screen controls
# ---------------------------------------------------------------------------

def get_screen_controls(
    conn,
    tenant_id: int,
    screen_code: str,
    lang: str = "en",
) -> list[ControlSummary]:
    """
    Return all voice-enabled controls for a given screen,
    with their example phrases in the requested language.
    """
    controls = []
    try:
        lang2 = (lang or "en").lower()[:2]
        with conn.cursor() as cur:
            # Get controls for this screen (tenant overrides globals)
            cur.execute(
                """SELECT r.control_code, r.label, r.action_code,
                          r.risk_level, r.voice_enabled
                     FROM crm.ui_control_registry r
                    WHERE r.voice_enabled = TRUE
                      AND r.enabled = TRUE
                      AND (r.screen_code = %s OR r.screen_code = 'global')
                      AND r.tenant_id IN (%s, 0)
                    ORDER BY r.tenant_id DESC, r.sort_order""",
                (screen_code, tenant_id),
            )
            rows = cur.fetchall()

            seen = set()
            for row in rows:
                r = dict(row)
                cc = r["control_code"]
                if cc in seen:
                    continue
                seen.add(cc)

                # Fetch example phrases
                cur.execute(
                    """SELECT synonym_text
                         FROM crm.ui_control_synonyms
                        WHERE control_code = %s
                          AND tenant_id IN (%s, 0)
                          AND language_code IN (%s, 'en')
                          AND status IN ('active', 'approved')
                        ORDER BY tenant_id DESC, synonym_type
                        LIMIT 5""",
                    (cc, tenant_id, lang2),
                )
                phrases = [x[0] for x in cur.fetchall()]

                # Short help
                cur.execute(
                    """SELECT short_help FROM crm.ui_control_voice_help
                        WHERE control_code = %s
                          AND tenant_id IN (%s, 0)
                          AND language_code IN (%s, 'en')
                        ORDER BY tenant_id DESC, language_code DESC
                        LIMIT 1""",
                    (cc, tenant_id, lang2),
                )
                help_row = cur.fetchone()
                short_help = help_row[0] if help_row else r["label"]

                controls.append(ControlSummary(
                    control_code=cc,
                    label=r["label"],
                    action_code=r.get("action_code", ""),
                    risk_level=r.get("risk_level", "safe"),
                    voice_enabled=r.get("voice_enabled", True),
                    example_phrases=phrases,
                    short_help=short_help,
                ))
    except Exception as e:
        print(f"[ai_control_bridge] get_screen_controls error: {e}")
    return controls


# ---------------------------------------------------------------------------
# Core voice resolver
# ---------------------------------------------------------------------------

def resolve_voice_command(
    text: str,
    conn,
    tenant_id: int,
    user_id: int,
    lang: str = "en",
    session_id: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    extra_args: Optional[dict] = None,
) -> VoiceResolveResult:
    """
    Resolve a voice utterance to a control_code + action_code.

    Resolution order (per spec):
      1. Current screen context  → narrows candidate set
      2. Exact synonym match     → fast exact lookup in ui_control_synonyms
      3. Entity alias match      → is the text a contact/job/task name?
      4. Semantic embedding      → cosine similarity via pgvector
      5. AI interpretation       → LLM picks from candidates (last resort)
      6. Clarification           → if still ambiguous, ask user

    NEVER executes an action. Returns VoiceResolveResult.
    Caller (e.g. voice_router in main.py) calls action_executor.execute_action().
    """
    lang2 = (lang or "en").lower()[:2]
    text_clean = text.strip().lower()

    # ── Step 1: Load current screen context ─────────────────────
    ctx = _get_voice_context(conn, tenant_id, user_id)
    screen_code = ctx.get("current_screen_code")
    current_module = ctx.get("current_module")
    selected_entity_type = ctx.get("selected_entity_type")
    selected_entity_id = ctx.get("selected_entity_id")

    # ── Step 2: Exact synonym match ──────────────────────────────
    result = _exact_synonym_match(
        conn, tenant_id, text_clean, lang2, screen_code
    )
    if result.resolved and result.confidence >= 0.95:
        return result

    # ── Step 3: Entity alias match ───────────────────────────────
    # (Is the text a known entity name? e.g. "call John Smith")
    entity_result = _entity_alias_match(
        conn, tenant_id, text_clean, lang2, screen_code
    )
    if entity_result.resolved:
        return entity_result

    # ── Step 4: Semantic embedding match ────────────────────────
    if openai_api_key:
        emb_result = _embedding_match(
            conn, tenant_id, text_clean, lang2, screen_code, openai_api_key
        )
        if emb_result.resolved and emb_result.confidence >= 0.80:
            return emb_result

    # ── Step 5: AI interpretation (last resort) ──────────────────
    if openai_api_key:
        candidates = _get_screen_candidates(conn, tenant_id, screen_code)
        ai_result = _ai_interpret(
            text, candidates, lang, ctx, openai_api_key
        )
        if ai_result.resolved and ai_result.confidence >= 0.70:
            return ai_result

    # ── Step 6: Clarification needed ────────────────────────────
    candidates = _get_screen_candidates(conn, tenant_id, screen_code)
    if candidates:
        top = candidates[:3]
        names = [c.get("label", c["control_code"]) for c in top]
        question = _clarification_question(names, lang2)
        return VoiceResolveResult(
            resolved=False,
            requires_clarification=True,
            clarification_question=question,
            candidates=top,
            resolution_method="clarification",
        )

    return VoiceResolveResult(
        resolved=False,
        error="no_match",
        resolution_method="failed",
    )


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _exact_synonym_match(
    conn, tenant_id: int, text: str, lang: str, screen_code: Optional[str]
) -> VoiceResolveResult:
    """
    Exact case-insensitive match against ui_control_synonyms,
    filtered by current screen if available.
    """
    try:
        with conn.cursor() as cur:
            # Try screen-scoped match first
            params = [tenant_id, lang, text]
            screen_filter = ""
            if screen_code:
                screen_filter = """
                    AND (r.screen_code = %s OR r.screen_code = 'global')"""
                params.append(screen_code)

            cur.execute(
                f"""SELECT s.control_code, r.action_code, r.risk_level,
                           r.label, s.confidence
                      FROM crm.ui_control_synonyms s
                      JOIN crm.ui_control_registry r
                        ON r.control_code = s.control_code
                       AND r.tenant_id IN (%s, 0)
                       AND r.voice_enabled = TRUE
                       AND r.enabled = TRUE
                     WHERE s.tenant_id IN (%s, 0)
                       AND s.language_code IN (%s, 'en')
                       AND lower(s.synonym_text) = lower(%s)
                       AND s.status IN ('active', 'approved')
                       {screen_filter}
                     ORDER BY r.tenant_id DESC, s.confidence DESC
                     LIMIT 3""",
                [tenant_id] + params,
            )
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            return VoiceResolveResult(resolved=False, resolution_method="exact_synonym")

        if len(rows) == 1:
            r = rows[0]
            return VoiceResolveResult(
                resolved=True,
                control_code=r["control_code"],
                action_code=r["action_code"],
                confidence=float(r.get("confidence", 1.0)),
                risk_level=r.get("risk_level", "safe"),
                resolution_method="exact_synonym",
            )

        # Multiple matches — need clarification
        return VoiceResolveResult(
            resolved=False,
            requires_clarification=True,
            candidates=rows,
            clarification_question=_clarification_question(
                [r.get("label", r["control_code"]) for r in rows[:3]], lang
            ),
            resolution_method="exact_synonym_ambiguous",
        )
    except Exception as e:
        return VoiceResolveResult(resolved=False, error=str(e))


def _entity_alias_match(
    conn, tenant_id: int, text: str, lang: str, screen_code: Optional[str]
) -> VoiceResolveResult:
    """
    Check if the utterance references a known entity by name or alias.

    Strategy:
      1. Strip action verb from start (call, find, message, ...)
      2. Look up remainder in contact_voice_aliases (exact + fuzzy)
      3. If 1 match  → resolved
      4. If 2+ match → requires_clarification with descriptive question
         "Myslíš Erika z irrigation project nebo Erika z Oxfordu?"
      5. Never guess when ambiguous

    After user confirms, caller should call learn_contact_alias() to
    save a learned_from_voice alias for faster future resolution.
    """
    # Verb → (control_code, action_code)
    VERB_PATTERNS = [
        (r'^(?:call|ring|phone|zavolej|volej|zadzwoń(?:\s+do)?)\s+(.+)$',
         'client.call_button', 'contact.call'),
        (r'^(?:whatsapp|message|text|napiš|zpráva\s+pro|zpráva\s+(?:pro\s+)?|napisz(?:\s+do)?)\s+(.+)$',
         'client.whatsapp_button', 'contact.whatsapp'),
        (r'^(?:open|show|detail|otevři|zobraz|ukáž|pokaż)\s+(.+)$',
         'client.edit_button', 'contact.view'),
        (r'^(?:find|search|look\s+up|najdi|hledej|vyhledej|znajdź)\s+(.+)$',
         'contacts.search_input', 'contact.search'),
        (r'^(?:note\s+for|add\s+note\s+(?:for|to)|poznámka\s+(?:pro|k)|přidej\s+poznámku\s+(?:pro|k))\s+(.+)$',
         'client.add_note_button', 'note.add'),
        (r'^(?:new\s+job\s+for|create\s+job\s+for|zakázka\s+pro|nová\s+zakázka\s+pro)\s+(.+)$',
         'client.add_job_button', 'job.create'),
        (r'^(?:invoice\s+for|faktura\s+pro|nová\s+faktura\s+pro)\s+(.+)$',
         'client.add_invoice_button', 'invoice.create'),
    ]

    for pattern, control_code, action_code in VERB_PATTERNS:
        m = re.match(pattern, text, re.IGNORECASE)
        if not m:
            continue
        entity_text = m.group(1).strip()

        contacts = _find_contacts_by_alias(conn, tenant_id, entity_text, lang)

        if not contacts:
            continue

        if len(contacts) == 1:
            c = contacts[0]
            return VoiceResolveResult(
                resolved=True,
                control_code=control_code,
                action_code=action_code,
                confidence=float(c.get("match_confidence", 0.90)),
                resolution_method="entity_alias",
                risk_level="safe",
                args={
                    "contact_id": c["id"],
                    "contact_name": c["display_name"],
                    "matched_alias": c.get("matched_alias", entity_text),
                },
            )

        # Multiple candidates — build a helpful disambiguation question
        question = _contact_disambiguation_question(contacts, lang)
        return VoiceResolveResult(
            resolved=False,
            requires_clarification=True,
            clarification_question=question,
            candidates=[
                {
                    "contact_id": c["id"],
                    "display_name": c["display_name"],
                    "hint": c.get("disambiguation_hint", ""),
                    "control_code": control_code,
                    "action_code": action_code,
                    "matched_alias": c.get("matched_alias", entity_text),
                }
                for c in contacts[:4]
            ],
            resolution_method="entity_alias_ambiguous",
        )

    return VoiceResolveResult(resolved=False)


def _find_contacts_by_alias(
    conn, tenant_id: int, text: str, lang: str
) -> list[dict]:
    """
    Find contacts matching `text` via:
      1. contact_voice_aliases (exact match, alias-aware)
      2. contacts.display_name / company_name (fuzzy fallback)

    Returns list of dicts enriched with disambiguation_hint:
    e.g. {"id": "uuid...", "display_name": "Erik Brown",
          "disambiguation_hint": "irrigation project",
          "match_confidence": 0.95, "matched_alias": "erik"}
    """
    results = []
    text_lower = text.lower().strip()
    lang2 = (lang or "en").lower()[:2]

    try:
        with conn.cursor() as cur:
            # ── Pass 1: exact alias match ────────────────────────
            cur.execute(
                """SELECT DISTINCT ON (c.id)
                          c.id, c.display_name,
                          c.first_name, c.last_name,
                          c.company_name, c.phone_primary, c.is_company,
                          a.alias_text   AS matched_alias,
                          a.confidence   AS match_confidence,
                          a.alias_type
                     FROM crm.contact_voice_aliases a
                     JOIN crm.contacts c
                       ON c.id = a.contact_id AND c.tenant_id = a.tenant_id
                    WHERE a.tenant_id = %s
                      AND a.is_active = TRUE
                      AND lower(a.alias_text) = %s
                      AND (a.language_code IS NULL
                           OR a.language_code = %s
                           OR a.language_code = 'en')
                    ORDER BY c.id, a.confidence DESC""",
                (tenant_id, text_lower, lang2),
            )
            rows = cur.fetchall()

            if not rows:
                # ── Pass 2: fuzzy display_name / company_name match ─
                cur.execute(
                    """SELECT id, display_name,
                              first_name, last_name,
                              company_name, phone_primary, is_company,
                              display_name AS matched_alias,
                              0.75 AS match_confidence,
                              'short_name' AS alias_type
                         FROM crm.contacts
                        WHERE tenant_id = %s
                          AND deleted_at IS NULL
                          AND (lower(display_name) ILIKE %s
                               OR (company_name IS NOT NULL AND lower(company_name) ILIKE %s))
                        ORDER BY lower(display_name) = %s DESC,
                                 length(display_name)
                        LIMIT 5""",
                    (tenant_id, f"%{text_lower}%", f"%{text_lower}%", text_lower),
                )
                rows = cur.fetchall()

        for row in rows:
            r = dict(row)
            # Build disambiguation hint: most recent job, or location, or company
            hint = _build_contact_hint(conn, tenant_id, r["id"], r)
            r["disambiguation_hint"] = hint
            results.append(r)

    except Exception as e:
        print(f"[ai_control_bridge] _find_contacts_by_alias error: {e}")

    return results


def _build_contact_hint(conn, tenant_id: int, contact_id, contact: dict) -> str:
    """
    Build a short disambiguation hint for a contact:
    most recent job name (via source_client_id), company name, or phone last 4.
    contact_id is UUID (crm.contacts.id).
    """
    hint_parts = []

    # Most recent job — link via source_client_id (contacts → clients → jobs)
    source_client_id = contact.get("source_client_id")
    if source_client_id:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT job_title FROM crm.jobs
                        WHERE tenant_id = %s AND client_id = %s
                          AND deleted_at IS NULL
                        ORDER BY created_at DESC LIMIT 1""",
                    (tenant_id, source_client_id),
                )
                job_row = cur.fetchone()
            if job_row:
                title = job_row[0] if isinstance(job_row, (list, tuple)) else job_row.get("job_title")
                if title:
                    hint_parts.append(title)
        except Exception:
            pass

    if not hint_parts:
        if contact.get("company_name"):
            hint_parts.append(contact["company_name"])
        elif contact.get("is_company") and contact.get("display_name"):
            # is_company flag but no company_name — use display_name as hint
            pass
        if contact.get("phone_primary"):
            phone = str(contact["phone_primary"])
            if len(phone) >= 4:
                hint_parts.append(f"...{phone[-4:]}")

    return ", ".join(hint_parts[:2]) if hint_parts else ""


def _contact_disambiguation_question(contacts: list[dict], lang: str) -> str:
    """
    Build a natural disambiguation question.

    EN: "Did you mean Erik (irrigation project) or Erik (Oxford)?",
    CS: "Myslíš Erika (irrigation project) nebo Erika (Oxford)?"
    """
    lang2 = (lang or "en").lower()[:2]
    parts = []
    for c in contacts[:4]:
        name = c.get("display_name", "")
        hint = c.get("disambiguation_hint", "")
        if hint:
            parts.append(f"{name} ({hint})")
        else:
            parts.append(name)

    if lang2 == "cs":
        if len(parts) == 2:
            return f"Myslíš {parts[0]} nebo {parts[1]}?"
        options = ", ".join(parts[:-1]) + f" nebo {parts[-1]}"
        return f"Myslíš {options}?"
    elif lang2 == "pl":
        if len(parts) == 2:
            return f"Czy chodzi Ci o {parts[0]} czy {parts[1]}?"
        options = ", ".join(parts[:-1]) + f" czy {parts[-1]}"
        return f"Czy chodzi Ci o {options}?"
    else:
        if len(parts) == 2:
            return f"Did you mean {parts[0]} or {parts[1]}?"
        options = ", ".join(parts[:-1]) + f" or {parts[-1]}"
        return f"Did you mean {options}?"


def learn_contact_alias(
    conn,
    tenant_id: int,
    contact_id,          # UUID (str) — crm.contacts.id
    alias_text: str,
    alias_type: str = "learned_from_voice",
    language_code: Optional[str] = None,
    voice_session_id: Optional[str] = None,
    user_id: Optional[int] = None,
    raw_text: Optional[str] = None,
    candidates_json: Optional[list] = None,
) -> bool:
    """
    Save a voice-learned alias for a contact.
    Called after user successfully confirms which contact they meant
    during a disambiguation clarification.

    Also logs to voice_entity_disambiguation_log.
    """
    alias_lower = alias_text.lower().strip()
    if len(alias_lower) < 2:
        return False

    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO crm.contact_voice_aliases
                       (tenant_id, contact_id, language_code, alias_text,
                        alias_type, source, confidence)
                   VALUES (%s, %s, %s, %s, %s, 'user_learned', 0.95)
                   ON CONFLICT (tenant_id, contact_id, alias_text)
                   DO UPDATE SET is_active = TRUE,
                                 updated_at = now(),
                                 confidence = GREATEST(
                                     crm.contact_voice_aliases.confidence, 0.95)""",
                (tenant_id, contact_id, language_code, alias_lower, alias_type),
            )

            # Log the disambiguation event
            cur.execute(
                """INSERT INTO crm.voice_entity_disambiguation_log
                       (tenant_id, user_id, voice_session_id, raw_text,
                        language_code, entity_type, candidates_json,
                        selected_contact_id, alias_learned, alias_type_learned)
                   VALUES (%s, %s, %s, %s, %s, 'contact', %s, %s, %s, %s)""",
                (
                    tenant_id, user_id, voice_session_id,
                    raw_text or alias_text, language_code,
                    json.dumps(candidates_json or []),
                    contact_id, alias_lower, alias_type,
                ),
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"[ai_control_bridge] learn_contact_alias error: {e}")
        return False


def _embedding_match(
    conn, tenant_id: int, text: str, lang: str, screen_code: Optional[str],
    openai_api_key: str
) -> VoiceResolveResult:
    """
    Embed the utterance and find the closest control using pgvector.
    Requires ui_control_embeddings to be populated.
    """
    try:
        embedding = _get_embedding(text, openai_api_key)
        if not embedding:
            return VoiceResolveResult(resolved=False)

        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
        with conn.cursor() as cur:
            screen_filter = ""
            # Param order matches SQL placeholders:
            # 1: vec_str  (SELECT similarity)
            # 2: tenant_id (r.tenant_id IN)
            # 3: tenant_id (e.tenant_id IN)
            # 4: lang      (e.language_code IN)
            # 5: vec_str   (WHERE cosine distance)
            # 6: 0.75      (threshold)
            # 7: screen_code (optional)
            params = [vec_str, tenant_id, tenant_id, lang, vec_str, 0.75]
            if screen_code:
                screen_filter = """
                    AND (r.screen_code = %s OR r.screen_code = 'global')"""
                params.append(screen_code)

            cur.execute(
                f"""SELECT e.control_code, r.action_code, r.risk_level,
                           1 - (e.embedding_vector <=> %s::vector) AS similarity
                      FROM crm.ui_control_embeddings e
                      JOIN crm.ui_control_registry r
                        ON r.control_code = e.control_code
                       AND r.tenant_id IN (%s, 0)
                       AND r.voice_enabled = TRUE
                       AND r.enabled = TRUE
                     WHERE e.tenant_id IN (%s, 0)
                       AND e.language_code IN (%s, 'en')
                       AND (1 - (e.embedding_vector <=> %s::vector)) > %s
                       {screen_filter}
                     ORDER BY similarity DESC
                     LIMIT 3""",
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            return VoiceResolveResult(resolved=False)

        best = rows[0]
        return VoiceResolveResult(
            resolved=True,
            control_code=best["control_code"],
            action_code=best["action_code"],
            confidence=float(best["similarity"]),
            risk_level=best.get("risk_level", "safe"),
            resolution_method="embedding",
            candidates=rows,
        )
    except Exception as e:
        return VoiceResolveResult(resolved=False, error=str(e))


def _ai_interpret(
    text: str,
    candidates: list[dict],
    lang: str,
    ctx: dict,
    openai_api_key: str,
) -> VoiceResolveResult:
    """
    Ask GPT to pick the most likely control from the candidate list.
    This is the LAST resort — result confidence is always capped at 0.85.
    """
    if not candidates or not openai_api_key:
        return VoiceResolveResult(resolved=False)

    try:
        import openai
        client = openai.OpenAI(api_key=openai_api_key)

        candidate_list = "\n".join(
            f"- control_code={c['control_code']}, label={c.get('label','')}, "
            f"action={c.get('action_code','')}"
            for c in candidates[:10]
        )
        screen = ctx.get("current_screen_code", "unknown")
        entity = ctx.get("selected_entity_type", "")

        messages = [
            {"role": "system", "content": (
                "You are a voice command resolver for a CRM mobile app. "
                "Given a user utterance and a list of UI controls available on the current screen, "
                "pick the single best matching control. "
                "Respond ONLY with a JSON object: "
                '{\"control_code\": \"...\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}. '
                "If nothing matches well, set confidence below 0.6."
            )},
            {"role": "user", "content": (
                f"User said: \"{text}\"\n"
                f"Current screen: {screen}\n"
                f"Selected entity: {entity}\n"
                f"Language: {lang}\n\n"
                f"Available controls:\n{candidate_list}\n\n"
                "Which control best matches the utterance?"
            )},
        ]
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)

        cc = parsed.get("control_code", "")
        conf = min(float(parsed.get("confidence", 0.0)), 0.85)  # cap AI confidence

        if not cc or conf < 0.60:
            return VoiceResolveResult(resolved=False, resolution_method="ai")

        # Find action_code from candidates
        match = next((c for c in candidates if c["control_code"] == cc), None)
        if not match:
            return VoiceResolveResult(resolved=False, resolution_method="ai")

        return VoiceResolveResult(
            resolved=True,
            control_code=cc,
            action_code=match.get("action_code", ""),
            confidence=conf,
            risk_level=match.get("risk_level", "safe"),
            resolution_method="ai",
        )
    except Exception as e:
        return VoiceResolveResult(resolved=False, error=str(e))


def _get_screen_candidates(conn, tenant_id: int, screen_code: Optional[str]) -> list[dict]:
    """Return all voice-enabled controls for a screen as raw dicts."""
    try:
        with conn.cursor() as cur:
            if screen_code:
                cur.execute(
                    """SELECT control_code, action_code, label, risk_level
                         FROM crm.ui_control_registry
                        WHERE tenant_id IN (%s, 0)
                          AND voice_enabled = TRUE AND enabled = TRUE
                          AND (screen_code = %s OR screen_code = 'global')
                        ORDER BY tenant_id DESC, sort_order
                        LIMIT 20""",
                    (tenant_id, screen_code),
                )
            else:
                cur.execute(
                    """SELECT control_code, action_code, label, risk_level
                         FROM crm.ui_control_registry
                        WHERE tenant_id IN (%s, 0)
                          AND voice_enabled = TRUE AND enabled = TRUE
                        ORDER BY tenant_id DESC, sort_order
                        LIMIT 30""",
                    (tenant_id,),
                )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# AI synonym generator
# ---------------------------------------------------------------------------

def generate_synonyms_for_control(
    conn,
    tenant_id: int,
    control_code: str,
    language_codes: list[str],
    openai_api_key: str,
    user_id: Optional[int] = None,
) -> Optional[str]:
    """
    Queue an AI synonym generation job for a control.
    Returns job_id (UUID str). AI writes drafts to ai_suggestion_review_queue.
    Admin must approve before synonyms become active.
    """
    job_id = str(uuid.uuid4())
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO crm.ai_synonym_generation_jobs
                       (id, tenant_id, requested_by_user_id, target_type,
                        target_code, language_code, status, input_context_json)
                   VALUES (%s, %s, %s, 'ui_control', %s, %s, 'pending', %s)""",
                (
                    job_id, tenant_id, user_id,
                    control_code,
                    ",".join(language_codes) if language_codes else None,
                    json.dumps({"language_codes": language_codes}),
                ),
            )
        conn.commit()

        # Run synchronously for now (could be made async via task queue)
        _run_synonym_generation_job(conn, tenant_id, job_id, openai_api_key)
        return job_id
    except Exception as e:
        print(f"[ai_control_bridge] generate_synonyms error: {e}")
        return None


def _run_synonym_generation_job(conn, tenant_id: int, job_id: str, openai_api_key: str):
    """Execute a synonym generation job and save drafts to review queue."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crm.ai_synonym_generation_jobs "
                "SET status = 'running', started_at = now() WHERE id = %s",
                (job_id,),
            )
            conn.commit()
            cur.execute(
                "SELECT target_code, language_code, input_context_json "
                "FROM crm.ai_synonym_generation_jobs WHERE id = %s",
                (job_id,),
            )
            job = dict(cur.fetchone())

        control_code = job["target_code"]
        lang_str = job.get("language_code") or "en,cs,pl"
        languages = [ln.strip() for ln in lang_str.split(",")]

        with conn.cursor() as cur:
            cur.execute(
                "SELECT label, description, help_text, action_code, module_name, screen_code "
                "FROM crm.ui_control_registry "
                "WHERE control_code = %s AND tenant_id IN (%s, 0) "
                "ORDER BY tenant_id DESC LIMIT 1",
                (control_code, tenant_id),
            )
            ctrl = cur.fetchone()
        if not ctrl:
            _fail_job(conn, job_id, "control_not_found")
            return
        ctrl = dict(ctrl)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT industry_description, company_name "
                "FROM crm.tenant_settings WHERE tenant_id = %s LIMIT 1",
                (tenant_id,),
            )
            tenant_row = cur.fetchone()
        tenant_ctx = dict(tenant_row) if tenant_row else {}

        import openai
        client = openai.OpenAI(api_key=openai_api_key)

        created_count = 0
        for lang_code in languages:
            try:
                prompt = (
                    "Generate natural voice command synonyms for a mobile CRM UI control.\n\n"
                    "Control: " + control_code + "\n"
                    "Label: " + ctrl.get('label', '') + "\n"
                    "Description: " + ctrl.get('description', '') + "\n"
                    "Module: " + ctrl.get('module_name', '') + "\n"
                    "Industry: " + tenant_ctx.get('industry_description', 'general business') + "\n\n"
                    "Generate 8-12 natural phrases a user might say to activate this control "
                    "in language: " + lang_code + " (BCP-47).\n"
                    "Include: short commands, natural phrases, industry-specific terms, slang.\n"
                    "Respond with a JSON array of strings only. No explanation."
                )
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.7,
                )
                raw = resp.choices[0].message.content.strip()
                raw = re.sub(r'^```(?:json)?\n?', '', raw)
                raw = re.sub(r'\n?```$', '', raw)
                phrases = json.loads(raw)
                for phrase in phrases:
                    if not isinstance(phrase, str) or not phrase.strip():
                        continue
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO crm.ai_suggestion_review_queue "
                            "(tenant_id, suggestion_type, target_type, target_code, "
                            " language_code, suggested_value, reason, confidence, status) "
                            "VALUES (%s, 'synonym', 'ui_control', %s, %s, %s, "
                            "        'AI generated synonym', 0.80, 'pending') "
                            "ON CONFLICT DO NOTHING",
                            (tenant_id, control_code, lang_code, phrase.strip()),
                        )
                    created_count += 1
                conn.commit()
            except Exception as e:
                print(f"[ai_control_bridge] synonym gen lang={lang_code}: {e}")

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crm.ai_synonym_generation_jobs "
                "SET status = 'completed', completed_at = now(), result_json = %s "
                "WHERE id = %s",
                (json.dumps({"synonyms_created": created_count}), job_id),
            )
        conn.commit()
    except Exception as e:
        _fail_job(conn, job_id, str(e))


def _fail_job(conn, job_id: str, error: str):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crm.ai_synonym_generation_jobs "
                "SET status = 'failed', completed_at = now(), error_message = %s "
                "WHERE id = %s",
                (error, job_id),
            )
        conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Synonym approval / rejection
# ---------------------------------------------------------------------------

def approve_synonym(conn, tenant_id: int, suggestion_id: str, user_id: int) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crm.ai_suggestion_review_queue "
                "SET status = 'approved', reviewed_by_user_id = %s, reviewed_at = now() "
                "WHERE id = %s AND tenant_id = %s AND status = 'pending' "
                "RETURNING suggestion_type, target_type, target_code, language_code, suggested_value",
                (user_id, suggestion_id, tenant_id),
            )
            row = cur.fetchone()
            if not row:
                return False
            row = dict(row)
            if row["suggestion_type"] == "synonym" and row["target_type"] == "ui_control":
                cur.execute(
                    "INSERT INTO crm.ui_control_synonyms "
                    "(tenant_id, control_code, language_code, synonym_text, "
                    " synonym_type, source, confidence, status, "
                    " created_by_ai, approved_by_user_id, approved_at) "
                    "VALUES (%s, %s, %s, %s, 'ai_generated', 'ai_suggested', "
                    "        0.80, 'active', TRUE, %s, now()) "
                    "ON CONFLICT (tenant_id, control_code, language_code, synonym_text) "
                    "DO UPDATE SET status = 'active', "
                    "             approved_by_user_id = EXCLUDED.approved_by_user_id, "
                    "             approved_at = now()",
                    (tenant_id, row["target_code"], row["language_code"],
                     row["suggested_value"], user_id),
                )
        conn.commit()
        return True
    except Exception as e:
        print(f"[ai_control_bridge] approve_synonym error: {e}")
        return False


def reject_synonym(conn, tenant_id: int, suggestion_id: str,
                   user_id: int, note: Optional[str] = None) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE crm.ai_suggestion_review_queue "
                "SET status = 'rejected', reviewed_by_user_id = %s, "
                "    reviewed_at = now(), review_note = %s "
                "WHERE id = %s AND tenant_id = %s AND status = 'pending'",
                (user_id, note, suggestion_id, tenant_id),
            )
        conn.commit()
        return True
    except Exception as e:
        print(f"[ai_control_bridge] reject_synonym error: {e}")
        return False


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _get_embedding(text: str, openai_api_key: str) -> Optional[list]:
    try:
        import openai
        client = openai.OpenAI(api_key=openai_api_key)
        resp = client.embeddings.create(model="text-embedding-3-small", input=text)
        return resp.data[0].embedding
    except Exception as e:
        print(f"[ai_control_bridge] embedding error: {e}")
        return None


def generate_embeddings_for_control(
    conn, tenant_id: int, control_code: str, openai_api_key: str
) -> int:
    count = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT language_code, synonym_text FROM crm.ui_control_synonyms "
                "WHERE control_code = %s AND tenant_id IN (%s, 0) "
                "AND status IN ('active', 'approved')",
                (control_code, tenant_id),
            )
            rows = cur.fetchall()
        for lang, text in rows:
            emb = _get_embedding(text, openai_api_key)
            if not emb:
                continue
            vec_str = "[" + ",".join(str(x) for x in emb) + "]"
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO crm.ui_control_embeddings "
                    "(tenant_id, control_code, language_code, source_text, "
                    " embedding_vector, source_type) "
                    "VALUES (%s, %s, %s, %s, %s::vector, 'synonym') "
                    "DO UPDATE SET embedding_vector = EXCLUDED.embedding_vector",
                    (tenant_id, control_code, lang, text, vec_str),
                )
            conn.commit()
            count += 1
    except Exception as e:
        print(f"[ai_control_bridge] generate_embeddings error: {e}")
    return count


# ---------------------------------------------------------------------------
# Clarification helpers
# ---------------------------------------------------------------------------

def _clarification_question(names: list, lang: str) -> str:
    if not names:
        return _t("I didn't understand. What would you like to do?",
                  "Nerozumel jsem. Co chces udelat?",
                  "Nie zrozumialem. Co chcesz zrobic?", lang)
    opts = ", ".join('"'  + n + '"'  for n in names)
    return _t("Did you mean: " + opts + "?",
              "Myslis: " + opts + "?",
              "Czy chodzilo Ci o: " + opts + "?", lang)


def _t(en: str, cs: str, pl: str, lang: str) -> str:
    code = (lang or "en").lower()[:2]
    return cs if code == "cs" else pl if code == "pl" else en
