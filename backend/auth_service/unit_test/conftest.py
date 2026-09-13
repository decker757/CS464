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
import pathlib
import secrets


def _load_repo_env() -> None:
    """Read the repo-root .env, the same file docker compose reads.

    Real connection details live there and it is gitignored, so nothing in the
    repository carries a credential. Anything already exported wins.
    """
    root_env = pathlib.Path(__file__).resolve().parents[3] / ".env"
    if not root_env.is_file():
        return
    for line in root_env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_repo_env()

_test_db = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not _test_db:
    raise RuntimeError(
        "No test database configured.\n"
        "Add TEST_DATABASE_URL to the repo-root .env, or export it:\n"
        "  export TEST_DATABASE_URL="
        "postgresql+asyncpg://USER:PASSWORD@localhost:PORT/cs464_test\n"
        "Start the database first with:  docker compose up -d db"
    )

# Set before any project module is imported: core.config.get_settings is cached
# on first call, and importing main.py triggers it.
os.environ["DATABASE_URL"] = _test_db
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
            tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
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
