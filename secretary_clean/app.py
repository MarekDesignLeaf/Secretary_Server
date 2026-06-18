"""FastAPI app factory for the clean Secretary backend foundation."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from secretary_clean.api.routes import (
    activities_compat,
    admin,
    assistant,
    auth,
    backup,
    bootstrap,
    calendar,
    catalogue,
    company,
    crm_v2,
    google_calendar,
    language,
    nature,
    tenant_pricing,
    tenant_rates,
    tools,
    translate,
    users,
    voice,
    voice_learning,
    voice_session,
    whatsapp,
    work_reports,
)
from secretary_clean.api.routes.bootstrap import version_router
from secretary_clean.catalogue.source_parser import load_catalogue
from secretary_clean.core.models import FirstInstallCreate
from secretary_clean.core.repository import InMemorySecretaryRepository

log = logging.getLogger(__name__)


# CORS: origins are driven exclusively by the ALLOWED_ORIGINS env variable.
# Default (env not set) allows local development only. No hardcoded
# production domains. Never use "*" together with allow_credentials.
_DEFAULT_DEV_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def _allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", _DEFAULT_DEV_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


_postgres_error: str | None = None


def _patch_ssl(url: str) -> str:
    """Add sslmode=require if not already present (Railway SSL postgres needs it)."""
    if "sslmode" not in url:
        sep = "&" if "?" in url else "?"
        return url + sep + "sslmode=require"
    return url


def _default_repository():
    global _postgres_error
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        patched_url = _patch_ssl(database_url)
        try:
            from secretary_clean.db.migration import run_migrations
            from secretary_clean.db.postgres_repository import PostgresSecretaryRepository
            run_migrations(patched_url)
            log.info("Using PostgresSecretaryRepository")
            _postgres_error = None
            return PostgresSecretaryRepository(patched_url)
        except Exception as exc:
            _postgres_error = str(exc)
            log.error(
                "Failed to connect/migrate PostgreSQL (%s); falling back to in-memory repository",
                exc,
            )
            return InMemorySecretaryRepository()

    log.warning("DATABASE_URL not set — using InMemorySecretaryRepository (data lost on restart)")
    return InMemorySecretaryRepository()


def _seed_from_env(repository) -> None:
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
    lang = os.environ.get("SEED_LANGUAGE", "cs-CZ")

    payload = FirstInstallCreate(
        company_name=company_name,
        first_admin_email=email,
        first_admin_password=password,
        first_admin_display_name=admin_name,
        country=country,
        currency=currency,
        timezone=timezone,
        default_internal_language_code=lang,
        default_customer_language_code=lang,
    )

    try:
        result = repository.create_first_install(payload, activity_defaults={})
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


def create_app(repository=None) -> FastAPI:
    repo = repository or _default_repository()
    app = FastAPI(title="Secretary Clean Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.state.repository = repo
    app.state.catalogue = load_catalogue()
    app.include_router(bootstrap.router, prefix="/api/v1")
    app.include_router(version_router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(company.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(catalogue.router, prefix="/api/v1")
    app.include_router(language.router, prefix="/api/v1")
    app.include_router(tenant_pricing.router, prefix="/api/v1")
    app.include_router(tenant_rates.router, prefix="/api/v1")
    app.include_router(crm_v2.router, prefix="/api/v1")
    app.include_router(work_reports.router, prefix="/api/v1")
    app.include_router(calendar.router, prefix="/api/v1")
    app.include_router(voice.router, prefix="/api/v1")
    app.include_router(voice_learning.router, prefix="/api/v1")
    app.include_router(google_calendar.router, prefix="/api/v1")
    app.include_router(voice_session.router, prefix="/api/v1")
    app.include_router(backup.router, prefix="/api/v1")
    app.include_router(activities_compat.router, prefix="/api/v1")
    app.include_router(assistant.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(whatsapp.router, prefix="/api/v1")
    app.include_router(translate.router, prefix="/api/v1")
    app.include_router(nature.router, prefix="/api/v1")
    app.include_router(tools.router, prefix="/api/v1")
    return app


app = create_app()
