"""FastAPI app factory for the clean Secretary backend foundation."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from secretary_clean.api.routes import auth, bootstrap, catalogue, company, crm, language, tenant_pricing, users, voice
from secretary_clean.catalogue.source_parser import load_catalogue
from secretary_clean.core.repository import InMemorySecretaryRepository

logger = logging.getLogger(__name__)


def _default_repository():
    """Select and return the appropriate repository implementation.

    Priority:
    1. DATABASE_URL env var  -> run migration, return PostgresSecretaryRepository
    2. SECRETARY_PERSISTENT=1 or RAILWAY_ENVIRONMENT set (without DATABASE_URL)
       -> JSON-file PersistentRepository (legacy fallback)
    3. Otherwise -> InMemorySecretaryRepository (tests / local dev without DB)
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            from secretary_clean.db.migration import run_migrations
            from secretary_clean.db.postgres_repository import PostgresSecretaryRepository
            run_migrations(database_url)
            logger.info("Using PostgresSecretaryRepository")
            return PostgresSecretaryRepository(database_url)
        except Exception as exc:
            logger.error(
                "Failed to connect/migrate PostgreSQL (%s); falling back to in-memory repository",
                exc,
            )
            return InMemorySecretaryRepository()

    use_persistent = (
        os.getenv("SECRETARY_PERSISTENT", "").lower() in ("1", "true", "yes")
        or os.getenv("RAILWAY_ENVIRONMENT") is not None
    )
    if use_persistent:
        from secretary_clean