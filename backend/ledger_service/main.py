"""Composition root for the ledger service.

Wires configuration, the database and the routes together. This is the only
place that decides which concrete implementations run.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller.errors import register_error_handlers
from controller.routes import router as ledger_router
from core.config import get_settings
from core.database import dispose_engine
from service import market_terms

logging.basicConfig(level=logging.INFO)

# Seconds, for connect and every read and write on the price bus (D-051).
# Without it a Redis that never answers makes the publish wait instead of
# raise, hanging a committed trade. Equals redis-py 8.1's default; stated so a
# version change cannot make it None.
_REDIS_TIMEOUT_SECONDS = 5.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Setup sits inside the `try`, so a failure partway (a mistyped REDIS_URL)
    # still reaches the disposal below. No DDL here: `ledger-migrate`
    # (migrate.py) brought the schema to head before this process started.
    # ADR 0020.
    try:
        # One client for the process (D-047). `from_url` dials lazily, so an
        # unreachable Redis does not stop boot, but parses eagerly, so a
        # mistyped REDIS_URL does (D-051).
        app.state.redis = redis.from_url(
            get_settings().redis_url,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
        )

        # One market_service client for the process too, because the gate
        # calls it on every trade that is not a replay (ADR 0017, D-047).
        # `async with` closes it before the `finally`.
        async with market_terms.open_client() as terms_client:
            app.state.terms_client = terms_client
            yield
    finally:
        # Nested so a failing `aclose()` cannot skip the disposal: a leaked
        # engine is every asyncpg connection this process opened.
        try:
            redis_client = getattr(app.state, "redis", None)
            if redis_client is not None:
                await redis_client.aclose()
        finally:
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
