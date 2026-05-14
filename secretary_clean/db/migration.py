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

# Extra tables required by the Postgres repository that are not in schema.sql
_EXTRA_DDL = """
CREATE EXTENSION IF NOT EXISTS citext;

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

    # Append extra DDL
    extra_statements = _split_statements(_rewrite_create_table(_rewrite_create_index(_EXTRA_DDL)))

    all_statements = statements + extra_statements

    conn = psycopg2.connect(database_url)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            for stmt in all_statements:
                stmt_preview = stmt[:80].replace("\n", " ")
                try:
                    cur.execute(stmt)
                    logger.debug("OK: %s", stmt_preview)
                except psycopg2.Error as exc:
                    # Log and re-raise — caller decides whether to abort startup
                    logger.error("Migration statement failed: %s\nError: %s", stmt_preview, exc)
                    conn.rollback()
                    raise
        conn.commit()
        logger.info("Database migrations completed successfully")
    finally:
        conn.close()


def _redact(url: str) -> str:
    """Redact password from a database URL for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
