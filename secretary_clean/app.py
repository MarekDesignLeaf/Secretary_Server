"""FastAPI app factory for the clean Secretary backend foundation.

This module is not a compatibility wrapper for the old Android or legacy API.
It defines the clean backend contract first.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from secretary_clean.api.routes import auth, bootstrap, catalogue, company, crm, language, tenant_pricing, users, voice
from secretary_clean.catalogue.source_parser import load_catalogue
from secretary_clean.core.models import FirstInstallCreate
from secretary_clean.core.repository import InMemorySecretaryRepository

log = logging.getLogger(__name__)


def _seed_from_env(repository: InMemorySecretaryRepository) -> None:
    """If no company/admin exists yet, create one from environment variables.

    Required env vars:
        SEED_ADMIN_EMAIL
        SEED_ADMIN_PASSWORD
        SEED_COMPANY_NAME

    Optional env vars:
        SEED_ADMIN_NAME       (default: Admin)
        SEED_COUNTRY          (default: CZ)
        SEED_CURRENCY         (default: CZK)
        SEED_TIMEZONE         (default: Europe/Prague)
        SEED_LANGUAGE         (default: cs-CZ)
    """
    status = repository.bootstrap_status()
    if status.is_ready:
        log.info("Seed skipped — repository already contains data.")
        return

    email = os.environ.get("SEED_ADMIN_EMAIL", "").strip()
    password = os.environ.get("SEED_ADMIN_PASSWORD", "").strip()
    company_name = os.environ.get("SEED_COMPANY_NAME", "").strip()

    if not email or not password or not company_name:
        log.warning(
            "Seed skipped — SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD / SEED_COMPANY_NAME not set. "
            "Server will start without data (is_ready=false)."
        )
        return

    admin_name = os.environ.get("SEED_ADMIN_NAME", "Admin")
    country = os.environ.get("SEED_COUNTRY", "CZ")
    currency = os.environ.get("SEED_CURRENCY", "CZK")
    timezone = os.environ.get("SEED_TIMEZONE", "Europe/Prague")
    language = os.environ.get("SEED_LANGUAGE", "cs-CZ")

    activity_defaults: dict[str, str] = {}

    payload = FirstInstallCreate(
        company_name=company_name,
        first_admin_email=email,
        first_admin_password=password,
        first_admin_display_name=admin_name,
        country=country,
        currency=currency,
        timezone=timezone,
        default_internal_language_code=language,
        default_customer_language_code=language,
    )

    try:
        result = repository.create_first_install(payload, activity_defaults=activity_defaults)
        log.info(
            "Seed complete — company=%s  admin=%s",
            result.company.id,
            result.admin.email,
        )
    except Exception as exc:
        log.error("Seed failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _seed_from_env(app.state.repository)
    yield


def create_app(repository: InMemorySecretaryRepository | None = None) -> FastAPI:
    repo = repository or InMemorySecretaryRepository()
    app = FastAPI(title="Secretary Clean Backend", version="0.1.0", lifespan=lifespan)
    app.state.repository = repo
    app.state.catalogue = load_catalogue()
    app.include_router(bootstrap.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(company.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(catalogue.router, prefix="/api/v1")
    app.include_router(language.router, prefix="/api/v1")
    app.include_router(tenant_pricing.router, prefix="/api/v1")
    app.include_router(crm.router, prefix="/api/v1")
    app.include_router(voice.router, prefix="/api/v1")
    return app

app = create_app()
