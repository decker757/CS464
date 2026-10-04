"""Shared fixtures.

Tests run against Postgres as `ledger_svc` under production grants: this
service's claims are about row locks, unique indexes and triggers, which SQLite
would pass while proving nothing. Start it with `docker compose up -d db`; the
suite rebuilds the separate `cs464_test` database.

Tokens are signed with PyJWT directly: this service has no minting code, and
the two services agree on a wire format, not an implementation.
"""

from __future__ import annotations

import os
import secrets
import uuid

from shared.testing import load_repo_env


# Before any project module is imported. `get_settings` is lru_cached, so the
# first call wins, and importing main.py triggers it.
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

import httpx  # noqa: E402
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
) -> str:
    """Sign a token the way the auth service does, for tests only.

    Defaults to TRADER, the ordinary caller here. The overridable issuer lets
    a test prove a foreign token is refused.
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
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def bearer(user_id: uuid.UUID, role: UserRole = UserRole.TRADER, **kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id, role, **kwargs)}"}


def terms_client_over(transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    """The production market_service client, with a test transport under it,
    so the real base URL, timeout and request building still run (#114)."""
    from service import market_terms  # noqa: PLC0415

    return market_terms.open_client(transport=transport)


class ManualClock:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def not_found_clock(monkeypatch: pytest.MonkeyPatch) -> ManualClock:
    """A fresh 404 memory for every test, on a clock the test moves. D-053.

    The memory lasts as long as the process, so without this one test's 404
    would answer another test's fetch of the same id. Built from
    `market_terms`'s own window and bound, so those are what is tested.
    """
    from core.not_found_cache import NotFoundCache  # noqa: PLC0415
    from service import market_terms  # noqa: PLC0415

    clock = ManualClock()
    monkeypatch.setattr(
        market_terms,
        "_recent_not_found",
        NotFoundCache(
            ttl_seconds=market_terms.NOT_FOUND_TTL_SECONDS,
            max_entries=market_terms.NOT_FOUND_MAX_ENTRIES,
            clock=clock,
        ),
    )
    return clock


@pytest.fixture
async def clean_database():
    """Drop and rebuild the schema, then hand over an empty database.

    Dropped rather than created-if-absent, so a new column reaches the test
    database. The rebuild also reinstalls the append-only trigger, an
    `after_create` event (ADR 0009). Not autouse, so the tests under core/
    never need Postgres.
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
async def app_under_test(clean_database):
    """The app, with the market_service client its lifespan would open.

    `ASGITransport` runs no lifespan, so `app.state.terms_client` does not
    exist under test. The dependency is overridden rather than the attribute
    set, because the override is the seam the routes read through. It is the
    production client, so a route test that does not stub `market_terms.fetch`
    makes a real connection attempt, never a silent success.

    `create_app` is imported here so the pure layers never construct the app.
    """
    from controller.dependencies import get_terms_client  # noqa: PLC0415
    from main import create_app  # noqa: PLC0415
    from service import market_terms  # noqa: PLC0415

    app = create_app()
    async with market_terms.open_client() as terms_client:
        app.dependency_overrides[get_terms_client] = lambda: terms_client
        yield app


@pytest.fixture
async def client(app_under_test):
    """An HTTP client bound to the app, for controller-layer tests."""
    async with AsyncClient(
        transport=ASGITransport(app=app_under_test), base_url="http://test"
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
    """From settings, not hardcoded, so it holds whatever STARTING_CREDITS is."""
    return get_settings().starting_credits


async def strip_outcomes(session, market_id: uuid.UUID) -> None:
    """Delete a market's outcome rows, leaving the book behind. Committed.

    The only way to reach `MarketBookIncomplete`: nothing in this service
    writes a book without its outcomes.
    """
    from sqlalchemy import delete  # noqa: PLC0415

    from model.entities import MarketOutcome  # noqa: PLC0415

    await session.execute(
        delete(MarketOutcome).where(MarketOutcome.market_id == market_id)
    )
    await session.commit()


async def idle_in_transaction() -> int:
    """Backends of this role in this database sitting `idle in transaction`,
    from Postgres's own view. D-043's third witness, beside the session and
    the pool."""
    from sqlalchemy import text  # noqa: PLC0415

    async with get_engine().connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND usename = current_user "
                    "AND state = 'idle in transaction' "
                    "AND pid <> pg_backend_pid()"
                )
            )
        ).scalar_one()
