"""Composition root for the auth service.

Wires configuration, the database and the routes together. This is the only
place that decides which concrete implementations run.
"""

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
from core.database import create_all, dispose_engine

# Imported for its side effect: registering the mappers on Base before
# create_all runs. Do not rely on another module pulling it in transitively.
from model import entities  # noqa: F401

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Replace with Alembic once [F-1] #41 shares this database.
    await create_all()
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

    # allow_credentials with an exact origin list, never "*", or the browser
    # silently drops the auth cookie.
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
