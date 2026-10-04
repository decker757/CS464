"""Composition root for the auth service: config, database and routes. [A-1..A-3]"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller.admin_routes import router as admin_router
from controller.errors import register_error_handlers
from controller.routes import router as auth_router
from core.config import get_settings
from core.database import dispose_engine

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # No DDL here: `auth-migrate` (migrate.py) brought the schema to head
    # before this process started. ADR 0020.
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CS464 Auth Service",
        version="0.1.0",
        description=(
            "Registration, login, logout and session refresh, plus the "
            "administration of other people's accounts. Owns users and "
            "credentials, and nothing else."
        ),
        lifespan=lifespan,
    )

    # ADR 0002: credentialed CORS needs an exact origin list, never "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(admin_router)

    @app.get("/health", tags=["ops"], summary="Liveness and readiness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
