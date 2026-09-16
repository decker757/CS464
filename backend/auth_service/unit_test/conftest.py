"""Shared fixtures.

Tests run against Postgres, not SQLite, because the two disagree in ways that
matter here: naive versus timezone-aware timestamps, and how a functional
unique index on lower(username) behaves. Testing on the engine we deploy is
the only way those differences show up before production.

Start the database with `docker compose up -d db` from the repo root. The
suite uses the separate `cs464_test` database created by
`scripts/init-test-db.sql`, so it can truncate without touching your data.

The layout mirrors the source: unit_test/core, unit_test/model,
unit_test/service and unit_test/controller. Database access is opt-in, so the
pure unit tests under core/ and model/ run with no database at all.
"""

from __future__ import annotations

import os
import secrets

from shared.testing import load_repo_env


# Before any project module is imported. `get_settings` is lru_cached, so the
# first call wins, and importing main.py triggers it. [F-6] #76 moved the
# reader itself to `shared/testing.py`; it was identical in all five suites.
load_repo_env()

# One variable per service now that market_service has its own suite and its
# own role. TEST_DATABASE_URL is the old single-service name, still honoured so
# an existing .env keeps working.
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

# Set before any project module is imported: core.config.get_settings is cached
# on first call, and importing main.py triggers it.
os.environ["DATABASE_URL"] = _test_db

# [4.4] #16. The role-change tests read back what this service appended, and
# they cannot do it on the connection that wrote it: auth_svc holds INSERT on
# audit.admin_actions and no SELECT, so that no service can read another's
# actions. They come in as audit_svc instead. Only the audit tests need this,
# so it is resolved here but never required.
_audit_db = os.environ.get("AUDIT_TEST_DATABASE_URL")
# Fresh per run. Nothing signed here outlives the process, and no key-shaped
# string needs to sit in the repository.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))
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

    Deliberately not autouse. Only `session` and `client` depend on it, so the
    pure unit tests under core/ and model/ never need Postgres running.

    Truncating beats dropping and recreating: far quicker per test, and each
    test still starts with no rows.
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
async def client(clean_database):
    """An HTTP client bound to the app, for controller-layer tests."""
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def registration_payload() -> dict[str, str]:
    return {
        "username": "ernest_t",
        "email": "ernest@example.com",
        "password": VALID_PASSWORD,
    }


_NO_AUDIT_READER = (
    "No audit reader configured.\n"
    "The audit-log tests read back what this service appended, and they must "
    "do it as audit_svc, because auth_svc holds INSERT on audit.admin_actions "
    "and no SELECT.\n"
    "Add AUDIT_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
    "  export AUDIT_TEST_DATABASE_URL="
    "postgresql+asyncpg://audit_svc:PASSWORD@localhost:PORT/cs464_test"
)


@pytest.fixture
async def audit_reader():
    """A session on `audit.admin_actions`, connected as the role that may read it.

    Separate engine, separate role, and deliberately not the one under test.

    Note what this fixture does NOT do: clean up. The log is append-only and no
    role holds DELETE or TRUNCATE, so rows from every previous test in this
    database are still present and always will be. Tests scope themselves by
    filtering on an actor id nothing else has used, which is a more honest
    assertion than a truncated table anyway: it is what reading a real audit
    log looks like.
    """
    from sqlalchemy.ext.asyncio import (  # noqa: PLC0415
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    if not _audit_db:
        pytest.fail(_NO_AUDIT_READER)

    engine = create_async_engine(_audit_db, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            yield s
    finally:
        await engine.dispose()


@pytest.fixture
async def admin_client(client: AsyncClient, session) -> AsyncClient:
    """A client signed in as an administrator, made the way the README says.

    Register, then promote with a manual UPDATE. That is not a shortcut around
    the endpoint under test: ADR 0007 keeps the first administrator a manual
    UPDATE permanently, because the account that may grant administrative
    authority cannot itself be granted it. Every administrator after this one
    is made through the route these tests exercise.

    No fresh login afterwards, deliberately. The cookie from registration still
    carries `role: trader`, and these routes work anyway, because this service
    reads the row rather than the claim. `test_a_promotion_binds_without_a_new_token`
    asserts that on purpose rather than leaving it as a happy accident here.
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


@pytest.fixture
async def target_user_id(session) -> str:
    """Somebody for an administrator to act on. Starts a trader, like everyone.

    Created through the service layer rather than through the API, because the
    client fixture holds one cookie jar: a second /auth/register on it would
    quietly re-authenticate the caller as this user and every admin-route test
    would then be asserting the wrong thing.
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
    )
    return str(user.id)
