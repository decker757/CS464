"""Shared fixtures.

Runs against Postgres as `audit_svc` under production grants (`docker compose
up -d db`). The suite cannot clean up: nothing holds DELETE or TRUNCATE, so
every test scopes itself to a fresh actor id. ADR 0006. Tokens are signed with
PyJWT here because this service has no minting code.
"""

from __future__ import annotations

import os
import secrets
import uuid

from shared.testing import load_repo_env


# Before any project module is imported: `get_settings` is cached on first call.
load_repo_env()

_test_db = os.environ.get("AUDIT_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not _test_db:
    raise RuntimeError(
        "No test database configured.\n"
        "Add AUDIT_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
        "  export AUDIT_TEST_DATABASE_URL="
        "postgresql+asyncpg://audit_svc:PASSWORD@localhost:PORT/cs464_test\n"
        "Start the database first with:  docker compose up -d db"
    )

# Before any project module is imported: `get_settings` is cached on first
# call, so a later assignment would never be read.
os.environ["DATABASE_URL"] = _test_db
# Fresh per run, so no key-shaped string sits in the repository.
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
) -> str:
    """Sign a token the way the auth service does, for tests only.

    `issuer` lets a test sign as a system this service must not trust.
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


def bearer(user_id: uuid.UUID, role: UserRole = UserRole.ADMIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id, role)}"}


@pytest.fixture
async def reachable_database():
    """Fail with instructions rather than a driver traceback.

    No schema rebuild: sql/02-schemas.sql owns the table. Not autouse, so the
    pure tests never reach Postgres.
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

    `create_app` is imported here so the pure layers never build the app.
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

    The only write in the suite; `audit_svc` holds INSERT for this alone.
    Timestamps are a second apart so ordering and cursors are deterministic.
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
