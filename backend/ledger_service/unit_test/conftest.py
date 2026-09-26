"""Shared fixtures.

Tests run against Postgres, not SQLite, as `ledger_svc` under the same grants
the service uses in production. That matters here for a reason the other suites
only half share: this service's claims are about what the database does under
concurrency — a row lock that makes two writers take turns, a unique index that
turns a race into a replay, a trigger that refuses an UPDATE. SQLite has no
opinion about any of that, so a suite running on it would pass while proving
nothing.

Start the database with `docker compose up -d db` from the repo root. The suite
uses the separate `cs464_test` database created by `sql/00-init.sh`, so it can
drop and rebuild the schema without touching development data.

Tokens here are minted with PyJWT directly rather than by importing anything
from the auth service. That is deliberate: this service has no minting code and
never will, so a test that signs its own token exercises the same path a real
request takes, and it stays honest about the fact that the two services agree
on a wire format rather than on an implementation.
"""

from __future__ import annotations

import os
import secrets
import uuid

from shared.testing import load_repo_env


# Before any project module is imported. `get_settings` is lru_cached, so the
# first call wins, and importing main.py triggers it. [F-6] #76 moved the
# reader itself to `shared/testing.py`; it was identical in all five suites.
load_repo_env()

_test_db = os.environ.get("LEDGER_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not _test_db:
    raise RuntimeError(
        "No test database configured.\n"
        "Add LEDGER_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
        "  export LEDGER_TEST_DATABASE_URL="
        "postgresql+asyncpg://ledger_svc:PASSWORD@localhost:PORT/cs464_test\n"
        "Start the database first with:  docker compose up -d db"
    )

# Set before any project module is imported: core.config.get_settings is cached
# on first call, and importing main.py triggers it.
os.environ["DATABASE_URL"] = _test_db
# Fresh per run. Nothing signed here outlives the process, and no key-shaped
# string needs to sit in the repository.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))

from datetime import UTC, datetime, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import (  # noqa: E402
    Base,
    dispose_engine,
    get_engine,
    get_session_factory,
)
from core.roles import UserRole  # noqa: E402

_UNREACHABLE = (
    "Cannot reach the test database.\n"
    "Start it with:  docker compose up -d db\n"
    "Or point LEDGER_TEST_DATABASE_URL at your own Postgres."
)


def mint_token(
    user_id: uuid.UUID,
    role: UserRole = UserRole.TRADER,
    *,
    username: str = "ernest_t",
    expires_in: int = 900,
    issuer: str | None = None,
    secret: str | None = None,
) -> str:
    """Sign a token the way the auth service does, for tests only.

    Defaults to TRADER, unlike the market service's suite, because a trader is
    the ordinary caller here: the routes that read your own balance are for
    everybody and only the two that read somebody else's need an admin.

    The overridable issuer and secret are what let a test prove this service
    rejects a token from a system it does not trust.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "username": username,
            "role": UserRole(role).value,
            "iss": issuer if issuer is not None else settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "jti": secrets.token_urlsafe(16),
        },
        secret if secret is not None else settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def bearer(user_id: uuid.UUID, role: UserRole = UserRole.TRADER, **kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id, role, **kwargs)}"}


@pytest.fixture
async def clean_database():
    """Rebuild the schema, then hand over an empty database.

    Dropped and recreated rather than created-if-absent, for the reason the
    market service's suite records: `create_all` only ever issues CREATE TABLE
    IF NOT EXISTS, so a column added to `model/entities.py` never reaches a
    test database that already has the table.

    This is also what reinstalls the append-only trigger, which is attached to
    `ledger.entries` as an after_create DDL event. A trigger written into
    `sql/` instead would be dropped by the first rebuild here and never come
    back, and `test_append_only.py` would pass in CI and fail in production —
    or, worse, the reverse.

    Deliberately not autouse. Only `session` and `client` depend on it, so the
    pure unit tests under core/ never need Postgres running.
    """
    from model import entities  # noqa: F401, PLC0415  - registers the mappers

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except SQLAlchemyError as exc:
        pytest.fail(f"{_UNREACHABLE}\n\n{exc}")

    yield

    await dispose_engine()


@pytest.fixture
async def session(clean_database):
    """A session for driving the service layer directly, without HTTP."""
    async with get_session_factory()() as s:
        yield s


@pytest.fixture
async def client(clean_database):
    """An HTTP client bound to the app, for controller-layer tests.

    `create_app` is imported here rather than at module scope so that running
    only the pure layers never constructs the application, in keeping with the
    rule that nothing below the controller knows HTTP exists.
    """
    from main import create_app  # noqa: PLC0415

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def trader_headers(user_id: uuid.UUID) -> dict[str, str]:
    return bearer(user_id, UserRole.TRADER)


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return bearer(uuid.uuid4(), UserRole.ADMIN)


@pytest.fixture
def starting_credits() -> Decimal:
    """Read from settings rather than hardcoded.

    A test that spelled 1000 out would be asserting the default rather than the
    behaviour, and would fail for whoever set STARTING_CREDITS in their .env.
    """
    return get_settings().starting_credits


async def strip_outcomes(session, market_id: uuid.UUID) -> None:
    """Delete a market's outcome rows, leaving the book behind. Committed.

    The state `MarketBookIncomplete` exists for, and the only way to reach it:
    nothing in this service writes a book without its outcomes, because
    `books.ensure_open` inserts both in one savepoint.

    Here rather than in each suite because three files built it inline and the
    guard's definition of "incomplete" has already moved once — from "no rows"
    to "fewer than `MIN_OUTCOMES`". Three hand-written setups is three places
    to find on the next move, and the one that is missed goes on passing for
    the wrong reason.
    """
    from sqlalchemy import delete  # noqa: PLC0415

    from model.entities import MarketOutcome  # noqa: PLC0415

    await session.execute(
        delete(MarketOutcome).where(MarketOutcome.market_id == market_id)
    )
    await session.commit()
