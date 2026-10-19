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


@pytest.fixture
async def target_user_id(session) -> str:
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
    )
    return str(user.id)
