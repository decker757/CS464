"""Composition root for the market service.

Wires configuration, the database and the routes together. This is the only
place that decides which concrete implementations run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller.errors import register_error_handlers
from controller.routes import router as market_router
from core.config import get_settings
from core.database import create_all, dispose_engine, get_session_factory

# Imported for its side effect: registering the mappers on Base before
# create_all runs. Do not rely on another module pulling it in transitively.
from model import entities  # noqa: F401
from service.sweeper import run_close_sweeper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Safe while this service owns the market schema alone. Move to Alembic
    # before the first column change against data worth keeping.
    await create_all()

    # [F-4] #44. The only background work this service does. It is started here
    # rather than anywhere nearer the sweep because this is the composition
    # root: `service/sweeper.py` is handed a session factory and an interval
    # and decides nothing about where either came from, which is what lets a
    # test drive it against its own factory without a running application.
    #
    # Not load-bearing. The task failing to start, or being switched off, makes
    # the status column stale; it cannot make a closed market tradeable. See
    # `service/closing.py`.
    settings = get_settings()
    sweeper: asyncio.Task[None] | None = None
    if settings.close_sweep_enabled:
        sweeper = asyncio.create_task(
            run_close_sweeper(
                get_session_factory(),
                interval_seconds=settings.close_sweep_seconds,
                batch_limit=settings.close_sweep_batch,
            ),
            name="close-sweeper",
        )
    else:
        logger.warning(
            "close sweeper disabled by CLOSE_SWEEP_ENABLED; market statuses "
            "will not be maintained on this replica"
        )

    try:
        yield
    finally:
        # Cancelled and awaited before the engine goes, in that order. Disposing
        # first would pull the pool out from under a sweep still in flight and
        # turn an ordinary shutdown into a stack trace.
        if sweeper is not None:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CS464 Market Service",
        version="0.1.0",
        description=(
            "Drafting and submitting prediction markets. Owns markets, their "
            "outcomes and their resolution sources, and knows nothing about "
            "users beyond the id in a signed access token."
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
    app.include_router(market_router)

    @app.get("/health", tags=["ops"], summary="Liveness and readiness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
