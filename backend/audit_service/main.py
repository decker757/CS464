"""Composition root for the audit service: config, database and routes. [4.3] #15"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller.errors import register_error_handlers
from controller.routes import router as audit_router
from core.config import get_settings
from core.database import dispose_engine

# No migrate step, unlike auth, market and ledger: sql/02-schemas.sql creates
# the table and the superuser owns it. ADR 0006.

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CS464 Audit Service",
        version="0.1.0",
        description=(
            "The immutable record of every administrative action, across every "
            "service. Reads only: entries are appended by the service that "
            "performed the action, in the same database transaction as the "
            "action itself, and nothing anywhere can edit or remove one."
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
    app.include_router(audit_router)

    @app.get("/health", tags=["ops"], summary="Liveness and readiness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
