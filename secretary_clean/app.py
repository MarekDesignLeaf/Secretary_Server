"""FastAPI app factory for the clean Secretary backend foundation.

This module is not a compatibility wrapper for the old Android or legacy API.
It defines the clean backend contract first.
"""

from __future__ import annotations

from fastapi import FastAPI

from secretary_clean.api.routes import auth, bootstrap, catalogue, company, crm, language, tenant_pricing, users, voice
from secretary_clean.catalogue.source_parser import load_catalogue
from secretary_clean.core.repository import InMemorySecretaryRepository


def create_app(repository: InMemorySecretaryRepository | None = None) -> FastAPI:
    app = FastAPI(title="Secretary Clean Backend", version="0.1.0")
    app.state.repository = repository or InMemorySecretaryRepository()
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
