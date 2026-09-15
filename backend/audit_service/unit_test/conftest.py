"""Shared fixtures.

Tests run against Postgres, not SQLite, as `audit_svc` under the same grants
the service uses in production. That matters more here than in the other two
suites: this service's central claim is about what the database refuses, and a
different engine refuses different things.

Start the database with `docker compose up -d db` from the repo root. The suite
uses the separate `cs464_test` database created by `sql/00-init.sh`.

One thing this suite cannot do, and should not be able to: clean up after
itself. `audit.admin_actions` is append-only, no role holds DELETE or TRUNCATE,
and a statement-level trigger refuses both even for the table's owner. So rows
written by every previous run are still there, and every test below scopes
itself to an actor id nothing else has used. That is what reading a real audit
log looks like, and a suite that could truncate the table would be a suite
running against weaker grants than production.

Tokens here are minted with PyJWT directly rather than by importing anything
from the auth service, for the same reason as the market service's suite: this
service has no minting code and never will, so a test that signs its own token
exercises the same path a real request takes.
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

_test_db = os.environ.get("AUDIT_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not _test_db:
    raise RuntimeError(
        "No test database configured.\n"
        "Add AUDIT_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
        "  export AUDIT_TEST_DATABASE_URL="
        "postgresql+asyncpg://audit_svc:PASSWORD@localhost:PORT/cs464_test\n"
        "Start the database first with:  docker compose up -d db"
    )

# Set before any project module is imported: core.config.get_settings is cached
# on first call, and importing main.py triggers it.
os.environ["DATABASE_URL"] = _test_db
# Fresh per run. Nothing signed here outlives the process, and no key-shaped
# string needs to sit in the repository.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))

from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import insert  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import dispose_engine, get_engine, get_session_factory  # noqa: E402
from core.roles import UserRole  # noqa: E402
from model.entities import AdminAction  # noqa: E402

_UNREACHABLE = (
    "Cannot reach the test database.\n"
    "Start it with:  docker compose up -d db\n"
    "Or point AUDIT_TEST_DATABASE_URL at your own Postgres.\n"
    "If the database predates [4.3] #15, apply "
    "sql/migrations/0002-audit-admin-actions.sql."
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
async def reachable_database():
    """Fail with instructions rather than a driver traceback.

    Deliberately not autouse, and deliberately not a schema rebuild. There is
    nothing to create — sql/02-schemas.sql owns this table — and nothing to
    drop. The pure unit tests under core/ and model/ never reach Postgres.
    """
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(AdminAction.__table__.select().limit(0))
    except SQLAlchemyError as exc:
        pytest.fail(f"{_UNREACHABLE}\n\n{exc}")

    yield

    await dispose_engine()


@pytest.fixture
async def session(reachable_database):
    """A session for driving the service layer directly, without HTTP."""
    async with get_session_factory()() as s:
        yield s


@pytest.fixture
async def client(reachable_database):
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
def trader_headers() -> dict[str, str]:
    return bearer(uuid.uuid4(), UserRole.TRADER)


@pytest.fixture
def actor_id() -> uuid.UUID:
    """An actor no other test has used, so a filter on it sees only this test's rows."""
    return uuid.uuid4()


@pytest.fixture
def seed(session: AsyncSession):
    """Append entries the way a real writer would, and return them newest first.

    This is the only place in the suite that writes. `audit_svc` holds INSERT
    for exactly this reason — so the reader's own tests can arrange a fixture
    without a second role's credentials — and no route reaches it.

    Timestamps are spaced a second apart and supplied explicitly, because the
    ordering and the keyset cursor are both under test and rows written in one
    transaction otherwise share a timestamp.
    """

    async def _seed(
        actor_id: uuid.UUID,
        count: int = 1,
        *,
        action_type: str = "market.submitted",
        username: str = "ernest_t",
        role: str = "admin",
        source_service: str = "market_service",
        target_id: uuid.UUID | None = None,
        target_label: str | None = None,
        reason: str | None = None,
        context: dict[str, Any] | None = None,
        first_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        base = first_at or datetime.now(UTC)
        rows = [
            {
                "id": uuid.uuid4(),
                "occurred_at": base + timedelta(seconds=index),
                "actor_id": actor_id,
                "actor_username": username,
                "actor_role": role,
                "action_type": action_type,
                "target_type": "market",
                "target_id": target_id or uuid.uuid4(),
                "target_label": target_label,
                "reason": reason,
                "context": context,
                "source_service": source_service,
            }
            for index in range(count)
        ]
        await session.execute(insert(AdminAction.__table__), rows)
        await session.commit()
        return list(reversed(rows))

    return _seed
