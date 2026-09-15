"""Shared fixtures.

Tests run against Postgres, not SQLite, as `market_svc` under the same grants
the service uses in production. The two engines disagree about naive versus
aware timestamps, and this service is almost entirely about timestamps, so
testing on the engine we deploy is the only way those differences show up
before production.

Start the database with `docker compose up -d db` from the repo root. The suite
uses the separate `cs464_test` database created by `sql/00-init.sh`, so it can
truncate without touching development data.

Tokens here are minted with PyJWT directly rather than by importing anything
from the auth service. That is deliberate: the market service has no minting
code and never will, so a test that signs its own token is exercising the same
path a real request takes, and it stays honest about the fact that the two
services agree on a wire format rather than on an implementation.
"""

from __future__ import annotations

import os
import pathlib
import secrets
import uuid


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

_test_db = os.environ.get("MARKET_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not _test_db:
    raise RuntimeError(
        "No test database configured.\n"
        "Add MARKET_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
        "  export MARKET_TEST_DATABASE_URL="
        "postgresql+asyncpg://market_svc:PASSWORD@localhost:PORT/cs464_test\n"
        "Start the database first with:  docker compose up -d db"
    )

# Set before any project module is imported: core.config.get_settings is cached
# on first call, and importing main.py triggers it.
os.environ["DATABASE_URL"] = _test_db
# Fresh per run. Nothing signed here outlives the process, and no key-shaped
# string needs to sit in the repository.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))

# [4.3] #15. A second connection, as the audit reader, purely so the suite can
# check what this service appended to `audit.admin_actions`.
#
# It needs its own role because `market_svc` genuinely cannot read that table —
# it holds INSERT and nothing else, which is what stops one service reading
# another's actions. A test that could read the log back through the service's
# own connection would be proving the grants are weaker than they are.
_audit_db = os.environ.get("AUDIT_TEST_DATABASE_URL")

from datetime import UTC, datetime, timedelta  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import NullPool  # noqa: E402
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
    "Or point MARKET_TEST_DATABASE_URL at your own Postgres."
)

_NO_AUDIT_READER = (
    "No audit reader configured.\n"
    "The audit-log tests read back what this service appended, and they must "
    "do it as audit_svc, because market_svc holds INSERT on "
    "audit.admin_actions and no SELECT.\n"
    "Add AUDIT_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
    "  export AUDIT_TEST_DATABASE_URL="
    "postgresql+asyncpg://audit_svc:PASSWORD@localhost:PORT/cs464_test"
)


def mint_token(
    user_id: uuid.UUID,
    role: UserRole = UserRole.ADMIN,
    *,
    username: str = "ernest_t",
    expires_in: int = 900,
    issuer: str | None = None,
    secret: str | None = None,
) -> str:
    """Sign a token the way the auth service does, for tests only.

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


def bearer(user_id: uuid.UUID, role: UserRole = UserRole.ADMIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id, role)}"}


@pytest.fixture
async def clean_database():
    """Rebuild the schema, then hand over an empty database.

    Dropped and recreated rather than created-if-absent. `create_all` only ever
    issues CREATE TABLE IF NOT EXISTS, so a column added to `model/entities.py`
    never reaches a test database that already has the table, and the suite
    fails with "column ... does not exist" on a model change that is perfectly
    correct. [1.2] #2 added two columns and hit exactly that.

    The cost is a drop and create per test, which is a few milliseconds for
    three small tables and buys a suite that always matches the models. When
    [F-1] #41 brings Alembic, this becomes "migrate to head" instead.

    Deliberately not autouse. Only `session` and `client` depend on it, so the
    pure unit tests under core/, model/ and the validation rules never need
    Postgres running.
    """
    from model import entities  # noqa: F401  - registers the mappers

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
def admin_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def admin_headers(admin_id: uuid.UUID) -> dict[str, str]:
    return bearer(admin_id, UserRole.ADMIN)


@pytest.fixture
def other_admin_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_admin_headers(other_admin_id: uuid.UUID) -> dict[str, str]:
    """A second, equally legitimate administrator. Must not see the first's drafts."""
    return bearer(other_admin_id, UserRole.ADMIN)


@pytest.fixture
def trader_headers() -> dict[str, str]:
    return bearer(uuid.uuid4(), UserRole.TRADER)


def future(**kwargs) -> str:
    """An ISO timestamp with an explicit offset, which the schema requires."""
    return (datetime.now(UTC) + timedelta(**kwargs)).isoformat()


@pytest.fixture
def submittable_payload() -> dict[str, object]:
    """A market that passes every rule in service/validation.py."""
    return {
        "draft_key": str(uuid.uuid4()),
        "status": "submitted",
        "question": "Will Singapore core inflation be below 2% for December 2026?",
        "description": "Measured on the first published print.",
        "outcomes": [{"label": "Yes"}, {"label": "No"}],
        "close_time": future(days=30),
        "resolution_time": future(days=45),
        "resolution_criteria": (
            "Resolves YES if the MAS core inflation print for December 2026, as "
            "first published, is strictly below 2.0%. Later revisions do not count."
        ),
        "resolution_sources": [
            {"url": "https://www.mas.gov.sg/statistics", "label": "MAS statistics"}
        ],
        # [1.2] #2. No liquidity_b: omitting it exercises the configured
        # default, which is the common case from the form.
        "seed_subsidy": 250,
    }


@pytest.fixture
async def audit_reader():
    """A session on `audit.admin_actions`, connected as the role that may read it.

    Separate engine, separate role, and deliberately not the one under test.

    Note what this fixture does NOT do: clean up. The log is append-only and no
    role holds DELETE or TRUNCATE, so rows from every previous test in this
    database are still present and always will be. Tests scope themselves by
    filtering on an actor id nothing else has used — see `_actor()` in
    unit_test/service/test_drafting.py — which is a more honest assertion than
    a truncated table anyway: it is what reading a real audit log looks like.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415

    if not _audit_db:
        pytest.fail(_NO_AUDIT_READER)

    engine = create_async_engine(_audit_db, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            yield s
    finally:
        await engine.dispose()
