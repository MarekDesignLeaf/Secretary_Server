"""Database migration helper for secretary_clean.

Reads schema.sql and applies CREATE TABLE / CREATE INDEX / CREATE UNIQUE INDEX
statements to the target database using IF NOT EXISTS so the script is safe to
run on every startup.

Additional tables not in schema.sql (e.g. clean_password_reset_tokens) are
created here so the PostgreSQL repository can store all data.

Usage:
    from secretary_clean.db.migration import run_migrations
    run_migrations(database_url)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_SCHEMA_FILE = Path(__file__).parent / "schema.sql"

# DDL that must run BEFORE schema.sql (extensions used by schema tables)
_PRE_DDL = """
CREATE EXTENSION IF NOT EXISTS citext;
"""

# Extra tables required by the Postgres repository that are not in schema.sql
_EXTRA_DDL = """
CREATE TABLE IF NOT EXISTS clean_password_reset_tokens (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS clean_prt_token_hash_idx
    ON clean_password_reset_tokens(token_hash);

CREATE TABLE IF NOT EXISTS clean_user_biometrics (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES clean_users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    biometric_hash TEXT NOT NULL,
    label TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, device_id)
);

CREATE TABLE IF NOT EXISTS clean_backup_manifests (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    created_by_user_id UUID NOT NULL REFERENCES clean_users(id),
    created_by_role TEXT NOT NULL,
    backup_scope TEXT NOT NULL,
    includes_db_reference BOOLEAN NOT NULL DEFAULT FALSE,
    storage_location TEXT NOT NULL,
    restore_token TEXT UNIQUE,
    restore_token_expires_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase A1: multi-industry support. A tenant may have any number of industries.
CREATE TABLE IF NOT EXISTS clean_tenant_industries (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    industry_code TEXT NOT NULL,
    subtype_code TEXT,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, industry_code)
);

CREATE INDEX IF NOT EXISTS clean_tenant_industries_company_idx
    ON clean_tenant_industries(company_id);

-- Phase A2: persistent voice sessions. Survives server restart / redeploy.
CREATE TABLE IF NOT EXISTS clean_voice_sessions (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    user_id UUID,
    state TEXT NOT NULL DEFAULT 'active',
    step TEXT NOT NULL DEFAULT 'client',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    touched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS clean_voice_sessions_company_idx
    ON clean_voice_sessions(company_id);

-- Phase A3: backend calendar events.
CREATE TABLE IF NOT EXISTS clean_calendar_events (
    id UUID PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES clean_companies(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    location TEXT,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ,
    all_day BOOLEAN NOT NULL DEFAULT FALSE,
    client_id TEXT,
    job_id TEXT,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS clean_calendar_events_company_idx
    ON clean_calendar_events(company_id);

CREATE INDEX IF NOT EXISTS clean_calendar_events_start_idx
    ON clean_calendar_events(company_id, start_at);
"""

# Column additions for existing tables (safe to run multiple times)
_ALTER_DDL = """
ALTER TABLE clean_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE clean_companies ADD COLUMN IF NOT EXISTS industry_group TEXT;
ALTER TABLE clean_companies ADD COLUMN IF NOT EXISTS industry_subtype TEXT;
ALTER TABLE tenant_operating_profile ADD COLUMN IF NOT EXISTS industry_group TEXT;
ALTER TABLE tenant_operating_profile ADD COLUMN IF NOT EXISTS industry_subtype TEXT;
ALTER TABLE clean_tenant_configuration ADD COLUMN IF NOT EXISTS industry_group TEXT;
ALTER TABLE clean_tenant_configuration ADD COLUMN IF NOT EXISTS industry_subtype TEXT;
"""

# Phase A1: backfill multi-industry table from legacy single industry_group.
# Idempotent: only inserts when a company has an industry but no rows yet.
_BACKFILL_DDL = """
INSERT INTO clean_tenant_industries (id, company_id, industry_code, subtype_code, is_primary)
SELECT gen_random_uuid(), c.id, c.industry_group, c.industry_subtype, TRUE
FROM clean_companies c
WHERE c.industry_group IS NOT NULL
  AND c.industry_group <> ''
  AND NOT EXISTS (
    SELECT 1 FROM clean_tenant_industries ti WHERE ti.company_id = c.id
  );
"""


def _rewrite_create_table(sql: str) -> str:
    """Insert IF NOT EXISTS into CREATE TABLE statements that lack it."""
    return re.sub(
        r"\bCREATE TABLE\b(?!\s+IF\s+NOT\s+EXISTS)",
        "CREATE TABLE IF NOT EXISTS",
        sql,
        flags=re.IGNORECASE,
    )


def _rewrite_create_index(sql: str) -> str:
    """Insert IF NOT EXISTS into CREATE [UNIQUE] INDEX statements that lack it."""
    return re.sub(
        r"\bCREATE (UNIQUE )?INDEX\b(?!\s+IF\s+NOT\s+EXISTS)",
        lambda m: f"CREATE {m.group(1) or ''}INDEX IF NOT EXISTS",
        sql,
        flags=re.IGNORECASE,
    )


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements on semicolons.

    Handles the fact that statement bodies may contain semicolons inside
    dollar-quoted strings or CHECK(...) blocks — for our schema those are
    simple enough that splitting on bare semicolons works reliably.
    """
    stmts = []
    for raw in sql.split(";"):
        stripped = raw.strip()
        if stripped:
            stmts.append(stripped + ";")
    return stmts


def run_migrations(database_url: str) -> None:
    """Apply schema.sql (idempotently) and any extra DDL to the database."""
    logger.info("Running database migrations against %s", _redact(database_url))

    raw_sql = _SCHEMA_FILE.read_text(encoding="utf-8")

    # Remove comment lines so they don't interfere with rewriting
    cleaned = "\n".join(
        line for line in raw_sql.splitlines() if not line.strip().startswith("--")
    )

    cleaned = _rewrite_create_table(cleaned)
    cleaned = _rewrite_create_index(cleaned)

    statements = _split_statements(cleaned)

    # Pre-DDL (extensions) must run before schema statements
    pre_statements = _split_statements(_PRE_DDL)

    # Extra tables not in schema.sql
    extra_statements = _split_statements(_rewrite_create_table(_rewrite_create_index(_EXTRA_DDL)))

    # ALTER statements for adding columns to existing tables (idempotent via IF NOT EXISTS)
    alter_statements = _split_statements(_ALTER_DDL)

    # Phase A1 backfill (must run AFTER tables + alters exist)
    backfill_statements = _split_statements(_BACKFILL_DDL)

    all_statements = pre_statements + statements + extra_statements + alter_statements + backfill_statements

    # Run every DDL statement independently with autocommit=True.
    # This means CREATE EXTENSION permission errors do NOT block table creation,
    # and ALTER TABLE runs even if earlier statements had issues.
    # All statements use IF NOT EXISTS so repeated runs are safe.
    conn = psycopg2.connect(database_url)
    errors = []
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for stmt in all_statements:
                stmt_preview = stmt[:80].replace("\n", " ")
                try:
                    cur.execute(stmt)
                    logger.debug("OK: %s", stmt_preview)
                except psycopg2.Error as exc:
                    logger.warning("DDL skipped (may be harmless): %s | %s", stmt_preview, exc)
                    errors.append((stmt_preview, str(exc)))
    finally:
        conn.close()

    if errors:
        logger.warning("Migration completed with %d skipped statements (see warnings above)", len(errors))
    else:
        logger.info("Database migrations completed successfully")


def _redact(url: str) -> str:
    """Redact password from a database URL for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
