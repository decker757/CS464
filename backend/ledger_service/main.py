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
from core.database import create_all, dispose_engine

# Imported for its side effect: registering the mappers on Base before
# create_all runs. Do not rely on another module pulling it in transitively.
# It also attaches the append-only trigger to `ledger.entries` as an
# after_create DDL event, so the guarantee ships with the table.
from model import entities  # noqa: F401

logging.basicConfig(level=logging.INFO)

# Seconds, for connect and for every read and write on the price bus. The same
# budget `service/market_terms.py::_TIMEOUT` gives the other outbound call on
# the trade path; see the lifespan below for why it is stated at all.
_REDIS_TIMEOUT_SECONDS = 5.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Safe while this service owns the ledger schema alone. Move to Alembic
    # before the first column change against data worth keeping — which for
    # this service means "before anybody has traded".
    await create_all()

    # One Redis client for the process. [F-9] #112. Built here rather than per
    # publish: a publish runs once per trade, so a client per call would be a
    # DNS lookup, a TCP handshake and a pool teardown on the hot path, for a
    # call whose entire purpose is to be cheap enough to fail silently.
    #
    # `from_url` does not connect — it builds a pool that dials lazily — so
    # this does not block startup on Redis being *reachable*, deliberately: an
    # unreachable bus costs one lost broadcast per trade, not a ledger that
    # will not boot.
    #
    # It does parse, eagerly, and that is also deliberate. A `REDIS_URL` that
    # is not a Redis URL — `http://redis:6379`, a typo in the scheme — raises
    # `ValueError` here and the ledger refuses to start. Unreachable and
    # mistyped are different failures: the first is an outage that ends, and
    # swallowing it costs broadcasts while it lasts; the second is a
    # configuration error that never ends, and swallowing it would kill every
    # broadcast forever with nothing in the logs of a healthy-looking service
    # to debug against. Failing at boot is how a typo gets found. Recorded as
    # "A mistyped `REDIS_URL` stops the ledger booting; an unreachable one
    # does not" in DECISIONS.md.
    #
    # Both socket timeouts are stated, for D-030's reason and at
    # `service/market_terms.py::_TIMEOUT`'s value: the publish runs after
    # [T-2] #22's commit, with the request's session still open, and a Redis
    # that accepts the connection and then never answers must not hold that
    # open for as long as the socket survives. Without a timeout the publish
    # does not raise, it waits, so `service/bus.py`'s `except Exception`
    # never fires and the swallow rule protects nothing.
    #
    # Like `_TIMEOUT`, these restate the library's own defaults — redis-py
    # 8.1.0 sets both to five seconds — so they change no behaviour today.
    # They are here because older redis-py left both at None, and a pin moved
    # in either direction should not silently decide how long a committed
    # trade can hang. `test_a_redis_that_never_answers_costs_a_bounded_wait_
    # and_no_exception` fails if the effective timeout ever becomes None.
    # `try` from here, not from the `yield`: the two failures this guards
    # against are `redis.from_url` raising on a mistyped URL — the documented
    # boot failure above, which happens *after* `create_all()` has opened the
    # engine — and `aclose()` raising on shutdown, which would leave every
    # asyncpg connection open behind it. Either one without this leaks the
    # engine for the life of the process.
    try:
        app.state.redis = redis.from_url(
            get_settings().redis_url,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
        )
        yield
    finally:
        redis_client = getattr(app.state, "redis", None)
        if redis_client is not None:
            await redis_client.aclose()
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
