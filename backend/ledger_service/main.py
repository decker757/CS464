"""Composition root for the ledger service.

Wires configuration, the database and the routes together. This is the only
place that decides which concrete implementations run.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller.errors import register_error_handlers
from controller.routes import router as ledger_router
from core.config import get_settings
from core.database import create_all, dispose_engine

# Imported for its side effect: registering the mappers on Base before
# create_all runs. Do not rely on another module pulling it in transitively.
# It also attaches the append-only trigger to `ledger.entries` as an
# after_create DDL event, so the guarantee ships with the table.
from model import entities  # noqa: F401

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Safe while this service owns the ledger schema alone. Move to Alembic
    # before the first column change against data worth keeping — which for
    # this service means "before anybody has traded".
    await create_all()
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CS464 Ledger Service",
        version="0.1.0",
        description=(
            "The record of every credit that has moved. Double-entry and "
            "append-only: a movement writes matching debit and credit rows "
            "that sum to zero, a balance is the sum of an account's entries "
            "rather than a column, and nothing anywhere can edit or remove an "
            "entry. Knows nothing about users beyond the id in a signed "
            "access token."
        ),
        lifespan=lifespan,
    )

    # allow_credentials with an exact origin list, never "*", or the browser
    # silently drops the auth cookie it needs to send here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(ledger_router)

    @app.get("/health", tags=["ops"], summary="Liveness and readiness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
