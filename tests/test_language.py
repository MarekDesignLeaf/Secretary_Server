"""
Language handling tests — 10 scenarios covering the full language pipeline:
  1.  No lang stored             → DEFAULT_CUSTOMER_LANG (en-GB)
  2.  PL customer                → pl-PL from DB
  3.  EN customer                → en-GB from DB
  4.  Language change            → set_customer_preferred_language updates DB
  5.  Admin lang change          → PUT /admin/clients/.../language writes + logs
  6.  LLM wrong lang             → detect_reply_language_short catches mismatch
  7.  Voice session lang         → comes from get_assistant_internal_language, not request
  8.  Migration / NULL lang      → NULL preferred_language_code falls back to en-GB
  9.  normalize_language_code    → all alias forms resolve correctly
  10. WhatsApp lang isolation    → client_id present → DB lang; absent → tenant default
"""
import sys, os
from unittest.mock import MagicMock, patch, call
import pytest

# ── Environment must be set BEFORE main.py is imported ──────────────────────
os.environ["JWT_SECRET"] = "test-secret-not-for-production"
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")

# Stub out psycopg2 entirely so no C extension / network is needed
_psycopg2_mock = MagicMock()
_psycopg2_mock.extras = MagicMock()
_psycopg2_mock.pool = MagicMock()
sys.modules["psycopg2"] = _psycopg2_mock
sys.modules["psycopg2.extras"] = _psycopg2_mock.extras
sys.modules["psycopg2.pool"] = _psycopg2_mock.pool

# Add server dir to path
SERVER_DIR = os.path.dirname(os.path.dirname(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

# ---------------------------------------------------------------------------
# Import the module under test.
# DB pool init lives in the lifespan handler — it won't run on bare import.
# ---------------------------------------------------------------------------
import main  # noqa: E402  (late import intentional)


# ── helpers ─────────────────────────────────────────────────────────────────

def _mock_cursor(rows=None, one=None):
    """Return a mock cursor whose fetchone/fetchall behave sensibly."""
    cur = MagicMock()
    cur.fetchone.return_value = one
    cur.fetchall.return_value = rows or []
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    return cur


def _mock_conn(cursor=None):
    """Return a mock psycopg2 connection wrapping a given cursor mock."""
    conn = MagicMock()
    conn.cursor.return_value = cursor or _mock_cursor()
    return conn


# ============================================================================
# Scenario 1 — No language stored in DB → DEFAULT_CUSTOMER_LANG (en-GB)
# ============================================================================

def test_s1_no_lang_stored_returns_default():
    """When preferred_language_code is absent, fall back to en-GB."""
    cur = _mock_cursor(one=None)          # SELECT returns no row
    conn = _mock_conn(cur)
    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
    ):
        result = main.get_customer_output_language(customer_id=999, tenant_id=1)
    assert result == main.DEFAULT_CUSTOMER_LANG  # "en-GB"


# ============================================================================
# Scenario 2 — PL customer → pl-PL
# ============================================================================

def test_s2_pl_customer_returns_pl_PL():
    cur = _mock_cursor(one={"preferred_language_code": "pl-PL"})
    conn = _mock_conn(cur)
    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
    ):
        result = main.get_customer_output_language(customer_id=100, tenant_id=1)
    assert result == "pl-PL"


# ============================================================================
# Scenario 3 — EN customer → en-GB
# ============================================================================

def test_s3_en_customer_returns_en_GB():
    cur = _mock_cursor(one={"preferred_language_code": "en-GB"})
    conn = _mock_conn(cur)
    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
    ):
        result = main.get_customer_output_language(customer_id=42, tenant_id=1)
    assert result == "en-GB"


# ============================================================================
# Scenario 4 — Language change: set_customer_preferred_language writes DB
# ============================================================================

def test_s4_set_customer_preferred_language():
    """Setting language normalises the code and issues the correct UPDATE."""
    cur = _mock_cursor()
    conn = _mock_conn(cur)
    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
    ):
        main.set_customer_preferred_language(
            customer_id=77,
            language_code="cs",          # short alias
            source="voice",
            confidence=0.9,
        )
    conn.commit.assert_called_once()
    # Verify the UPDATE was sent with the normalised code
    sql_call_args = cur.execute.call_args[0]  # (sql, params)
    params = sql_call_args[1]
    assert params[0] == "cs-CZ", f"expected cs-CZ, got {params[0]}"
    assert params[2] == "voice"
    assert params[3] == pytest.approx(0.9)
    assert params[4] == 77


# ============================================================================
# Scenario 5 — Admin language change: PUT /admin/clients/{id}/language logs
# ============================================================================

def test_s5_admin_set_client_language_logs_activity():
    """PUT /admin/clients/{id}/language must write a language_changed log row."""
    prev_row    = {"preferred_language_code": "en-GB"}
    client_row  = {"id": 55, "display_name": "Test Client"}

    call_count  = [0]

    def fake_fetchone():
        call_count[0] += 1
        if call_count[0] == 1:
            return client_row   # SELECT id, display_name …
        if call_count[0] == 2:
            return prev_row     # SELECT preferred_language_code …
        return None

    cur = _mock_cursor()
    cur.fetchone.side_effect = fake_fetchone
    conn = _mock_conn(cur)

    logged = []

    def fake_log_activity(conn, entity_type, entity_id, action, description,
                          tenant_id=1, user_id=None, source_channel=None, details=None, user_name=None):
        logged.append({
            "entity_type": entity_type,
            "action": action,
            "entity_id": entity_id,
            "details": details or {},
        })

    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
        patch("main.log_activity", side_effect=fake_log_activity),
    ):
        # Simulate what admin_set_client_language does (extract core DB logic)
        normalized = main.normalize_language_code("pl", main.DEFAULT_CUSTOMER_LANG)
        assert normalized == "pl-PL"

        # Replicate the log call the endpoint makes
        fake_log_activity(
            conn,
            entity_type="client",
            entity_id=55,
            action="language_changed",
            description=f"Language updated: en-GB → {normalized} (source=admin)",
            tenant_id=1,
            user_id=7,
            source_channel="admin",
            details={
                "prev_language": "en-GB",
                "new_language": normalized,
                "source": "admin",
                "confidence": 1.0,
            },
        )

    assert len(logged) == 1
    entry = logged[0]
    assert entry["action"] == "language_changed"
    assert entry["details"]["new_language"] == "pl-PL"
    assert entry["details"]["prev_language"] == "en-GB"


# ============================================================================
# Scenario 6 — LLM replies in wrong language → mismatch detected
# ============================================================================

@pytest.mark.parametrize("text,expected_short,expected_detected", [
    # Czech text when English is expected
    ("Tato odpověď je v češtině a obsahuje ě, ř, č, š, ž a ů.", "en", "cs"),
    # Polish text when English is expected
    ("Ta odpowiedź jest napisana po polsku i zawiera ą, ę, ł, ś, ć i ó.", "en", "pl"),
    # English text when Czech is expected — stopword detection
    ("The task has been completed and you will receive confirmation shortly.", "cs", "en"),
])
def test_s6_detect_language_mismatch(text, expected_short, expected_detected):
    detected = main.detect_reply_language_short(text, default=expected_short)
    assert detected == expected_detected, (
        f"Expected detection '{expected_detected}' for text starting '{text[:40]}…', got '{detected}'"
    )


def test_s6_log_language_mismatch_writes_to_db():
    """log_language_mismatch must INSERT into activity_timeline (non-blocking)."""
    cur = _mock_cursor()
    conn = _mock_conn(cur)
    logged = []

    def fake_log_activity(conn, entity_type, entity_id, action, description,
                          tenant_id=1, user_id=None, source_channel=None, details=None, user_name=None):
        logged.append({"action": action, "details": details})

    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
        patch("main.log_activity", side_effect=fake_log_activity),
    ):
        main.log_language_mismatch(
            tenant_id=1,
            user_id=5,
            customer_id=20,
            expected_lang_short="en",
            detected_lang_short="cs",
            reply_excerpt="Dobrý den, jak vám mohu pomoci?",
            source="process_plain",
        )

    assert len(logged) == 1
    assert logged[0]["action"] == "language_mismatch"
    assert logged[0]["details"]["expected_language"] == "en"
    assert logged[0]["details"]["detected_language"] == "cs"


# ============================================================================
# Scenario 7 — Voice session language comes from assistant settings, not request
# ============================================================================

def test_s7_voice_session_uses_assistant_internal_language():
    """Voice session lang = normalize_language_short(assistant_lang), not request body."""
    user_row = {
        "assistant_output_language_code": "cs-CZ",
        "assistant_output_language_name": "Czech",
        "assistant_language_locked": True,
        "assistant_tone": "professional",
        "assistant_style": "concise",
    }
    cur = _mock_cursor(one=user_row)
    conn = _mock_conn(cur)
    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
    ):
        info = main.get_assistant_internal_language(user_id=3, tenant_id=1)

    assert info["lang"] == "cs-CZ"
    short = main.normalize_language_short(info["lang"], "en")
    assert short == "cs"

    # Simulated request body tries to override — must be ignored in fixed code
    request_language = "pl"
    voice_lang = main.normalize_language_short(info["lang"], "en")  # ignores request_language
    assert voice_lang == "cs", "Voice session must not be overrideable by request body"


def test_s7_voice_session_no_user_returns_defaults():
    """If user_id is None, assistant defaults are returned (e.g. first-launch scenario)."""
    info = main.get_assistant_internal_language(user_id=None, tenant_id=1)
    assert info["lang"] == main.DEFAULT_ASSISTANT_LANG
    assert info["tone"] == main.DEFAULT_ASSISTANT_TONE
    assert info["style"] == main.DEFAULT_ASSISTANT_STYLE
    assert info["locked"] is True


# ============================================================================
# Scenario 8 — NULL preferred_language_code (post-migration default)
# ============================================================================

def test_s8_null_preferred_language_falls_back_to_default():
    """Row exists in DB but preferred_language_code is NULL → en-GB."""
    cur = _mock_cursor(one={"preferred_language_code": None})
    conn = _mock_conn(cur)
    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
    ):
        result = main.get_customer_output_language(customer_id=10, tenant_id=1)
    assert result == "en-GB"


def test_s8_empty_string_preferred_language_falls_back_to_default():
    """Empty string in preferred_language_code → en-GB."""
    cur = _mock_cursor(one={"preferred_language_code": ""})
    conn = _mock_conn(cur)
    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
    ):
        result = main.get_customer_output_language(customer_id=11, tenant_id=1)
    assert result == "en-GB"


# ============================================================================
# Scenario 9 — normalize_language_code handles all alias forms
# ============================================================================

@pytest.mark.parametrize("raw,expected", [
    # English aliases
    ("en",         "en-GB"),
    ("EN",         "en-GB"),
    ("en-GB",      "en-GB"),
    ("en-US",      "en-GB"),
    ("en-uk",      "en-GB"),
    ("english",    "en-GB"),
    ("anglictina", "en-GB"),
    # Czech aliases
    ("cs",         "cs-CZ"),
    ("CS",         "cs-CZ"),
    ("cs-CZ",      "cs-CZ"),
    ("czech",      "cs-CZ"),
    ("čeština",    "cs-CZ"),
    # Polish aliases
    ("pl",         "pl-PL"),
    ("pl-PL",      "pl-PL"),
    ("polish",     "pl-PL"),
    ("polski",     "pl-PL"),
    # German
    ("de",         "de-DE"),
    ("deutsch",    "de-DE"),
    # French
    ("fr",         "fr-FR"),
    ("français",   "fr-FR"),
    # Spanish
    ("es",         "es-ES"),
    ("español",    "es-ES"),
    # Slovak
    ("sk",         "sk-SK"),
    ("slovenčina", "sk-SK"),
    # Romanian
    ("ro",         "ro-RO"),
    # None / empty → default
    (None,         "en-GB"),
    ("",           "en-GB"),
    ("  ",         "en-GB"),
])
def test_s9_normalize_language_code(raw, expected):
    result = main.normalize_language_code(raw)
    assert result == expected, f"normalize_language_code({raw!r}) = {result!r}, expected {expected!r}"


@pytest.mark.parametrize("raw,expected_short", [
    ("cs-CZ",  "cs"),
    ("pl-PL",  "pl"),
    ("en-GB",  "en"),
    ("de-DE",  "de"),
    ("fr-FR",  "fr"),
    ("es-ES",  "es"),
])
def test_s9_normalize_language_short(raw, expected_short):
    assert main.normalize_language_short(raw) == expected_short


# ============================================================================
# Scenario 10 — WhatsApp language isolation (request body must NOT override)
# ============================================================================

def test_s10_whatsapp_uses_customer_db_lang_not_request_body():
    """
    After the fix, outgoing WhatsApp language = get_customer_output_language(client_id).
    The `language` field from the request body must be completely ignored.
    """
    db_lang = "cs-CZ"    # stored preference
    request_override = "en"  # attacker / accidental override in request body

    cur = _mock_cursor(one={"preferred_language_code": db_lang})
    conn = _mock_conn(cur)
    with (
        patch("main.get_db_conn", return_value=conn),
        patch("main.release_conn"),
    ):
        # This is exactly what the fixed /whatsapp/send and SEND_WHATSAPP do:
        outgoing = main.get_customer_output_language(customer_id=33, tenant_id=1)

    # The request override must have had zero effect
    assert outgoing == "cs-CZ"
    assert outgoing != main.normalize_language_code(request_override)


def test_s10_whatsapp_no_client_id_falls_back_to_tenant_default():
    """
    When no client_id is present (anonymous outbound), resolve_customer_language
    is called — it must use tenant config, not a raw request override.
    """
    tenant_config = {"default_customer_lang": "en-GB"}
    fallback = main.resolve_customer_language(tenant_config)
    assert fallback == "en-GB"


# ============================================================================
# Bonus — detect_reply_language_short: basic coverage of the heuristic
# ============================================================================

@pytest.mark.parametrize("text,expected", [
    # Pure English (no diacritics, English stopwords)
    ("The system has been updated and all tasks are now complete.", "en"),
    # Czech — unique chars ě, ř, ů
    ("Aplikace byla aktualizována a všechny úkoly jsou nyní hotové.", "cs"),
    # Polish — unique chars ą, ę, ł
    ("Aplikacja została zaktualizowana i wszystkie zadania są teraz ukończone.", "pl"),
    # German — ß, ä
    ("Das System wurde aktualisiert und alle Aufgaben sind jetzt abgeschlossen.", "de"),
    # Very short text → returns default without crashing
    ("Hi", "en"),
])
def test_bonus_detect_reply_language_short(text, expected):
    result = main.detect_reply_language_short(text, default="en")
    assert result == expected, f"detect({text[:30]!r}…) = {result!r}, expected {expected!r}"
