"""Shared fixtures.

Runs against Postgres, not SQLite, which disagrees about naive timestamps and
functional unique indexes (`docker compose up -d db`, database `cs464_test`).
Database access is opt-in, so core/ and model/ tests need none.
"""

from __future__ import annotations

import os
import secrets

import pytest

# Before the first import of shared.testing: pytest rewrites asserts only in
# modules it sees imported after this call, so without it the shared helpers'
# asserts fail with no diff.
pytest.register_assert_rewrite("shared.testing")

from shared.testing import load_repo_env, open_audit_reader  # noqa: E402


# Before any project module is imported: `get_settings` is cached on first call.
load_repo_env()

# AUTH_TEST_DATABASE_URL first; TEST_DATABASE_URL is accepted as a fallback.
_test_db = (
    os.environ.get("AUTH_TEST_DATABASE_URL")
    or os.environ.get("TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
)
if not _test_db:
    raise RuntimeError(
        "No test database configured.\n"
        "Add AUTH_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
        "  export AUTH_TEST_DATABASE_URL="
        "postgresql+asyncpg://auth_svc:PASSWORD@localhost:PORT/cs464_test\n"
        "Start the database first with:  docker compose up -d db"
    )

# Before any project module is imported: `get_settings` is cached on first
# call, so a later assignment would never be read.
os.environ["DATABASE_URL"] = _test_db

# Fresh per run, so no key-shaped string sits in the repository.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))
os.environ.setdefault("LEDGER_SERVICE_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("PASSWORD_MIN_LENGTH", "12")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from core.database import (  # noqa: E402
    Base,
    dispose_engine,
    get_engine,
    get_session_factory,
)
from main import create_app  # noqa: E402

VALID_PASSWORD = "correct-horse-battery-staple"

_UNREACHABLE = (
    "Cannot reach the test database.\n"
    "Start it with:  docker compose up -d db\n"
    "Or point TEST_DATABASE_URL at your own Postgres."
)


@pytest.fixture
async def clean_database():
    """Create the schema if absent, then empty every table.

    Not autouse, so the pure tests never need Postgres. Truncating is far
    quicker than dropping and recreating.
    """
    from model import entities  # noqa: F401  - registers the mappers

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            tables = ", ".join(
                f"{t.schema}.{t.name}" if t.schema else t.name
                for t in Base.metadata.sorted_tables
            )
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
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
def app(attempt_limiter):
    """The app with a test limiter. A test needing small limits replaces
    `app.state.attempt_limiter` before its first request."""
    application = create_app()
    application.state.attempt_limiter = attempt_limiter
    return application


@pytest.fixture
async def client(clean_database, app):
    """An HTTP client bound to the app, for controller-layer tests."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def registration_payload() -> dict[str, str]:
    return {
        "username": "ernest_t",
        "email": "ernest@example.com",
        "password": VALID_PASSWORD,
    }


@pytest.fixture
async def audit_reader():
    """The audit log, read as `audit_svc`: `auth_svc` holds INSERT on it and no SELECT."""
    async with open_audit_reader(writer_role="auth_svc") as session:
        yield session


@pytest.fixture
async def admin_client(client: AsyncClient, session) -> AsyncClient:
    """A client signed in as an administrator promoted by manual UPDATE.

    The first admin is always made by hand (ADR 0007). No fresh login: the
    cookie still says `trader`, and these routes read the row instead.
    """
    await client.post(
        "/auth/register",
        json={
            "username": "admin_one",
            "email": "admin.one@example.com",
            "password": VALID_PASSWORD,
        },
    )
    await session.execute(
        text("UPDATE auth.users SET role = 'admin' WHERE lower(username) = 'admin_one'")
    )
    await session.commit()
    return client


# A documentation-range address (RFC 5737), so no test touches a real one.
TEST_IP = "198.51.100.7"


@pytest.fixture
async def target_user_id(session, attempt_limiter) -> str:
    """A trader for an administrator to act on.

    Registered through the service layer: a second /auth/register on the
    client would replace the admin's cookie with this user's.
    """
    from model.schemas import RegisterRequest  # noqa: PLC0415
    from service import auth_service  # noqa: PLC0415

    user, _ = await auth_service.register(
        session,
        RegisterRequest(
            username="michelle_l",
            email="michelle@example.com",
            password=VALID_PASSWORD,
        ),
        limiter=attempt_limiter,
        client_ip=TEST_IP,
    )
    return str(user.id)


async def _delete_keys(client, prefix: str) -> None:
    async for key in client.scan_iter(f"{prefix}:*"):
        await client.delete(key)


@pytest.fixture
async def make_limiter():
    """A factory for limiters over the real Redis, each under its own prefix.

    `overrides` replace named limits (e.g. `login_ip=Limit("login-ip", 3, 60)`)
    so a test can use a small count. Every key a limiter wrote is deleted at
    teardown; a fresh prefix keeps tests and parallel runs from sharing counts.
    """
    from dataclasses import replace  # noqa: PLC0415
    from uuid import uuid4  # noqa: PLC0415

    from core.cache import open_redis  # noqa: PLC0415
    from core.config import get_settings  # noqa: PLC0415
    from service.attempt_limits import AttemptLimiter, AttemptLimits  # noqa: PLC0415

    opened: list[tuple] = []

    def build(**overrides) -> AttemptLimiter:
        client = open_redis(get_settings())
        prefix = f"auth:rl:test:{uuid4().hex}"
        limits = replace(AttemptLimits.from_settings(get_settings()), **overrides)
        opened.append((client, prefix))
        return AttemptLimiter(client, limits, prefix)

    yield build

    for client, prefix in opened:
        await _delete_keys(client, prefix)
        await client.aclose()


@pytest.fixture
async def attempt_limiter(make_limiter):
    """One limiter with the configured limits."""
    return make_limiter()
