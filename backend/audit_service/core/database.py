"""Async engine, session factory, and the FastAPI session dependency.

Two things are missing here on purpose, and both follow from what this service
is allowed to do.

There is no `create_all`. `audit.admin_actions` is created by
`sql/02-schemas.sql` and owned by the superuser, so this service has no CREATE
on its own schema and could not make the table even if it tried. That is the
point: the log is shared infrastructure that several services append to, and
the service that reads it should not be the one that defines it.

There is no write path either. `service/` issues SELECTs and nothing else. The
role holds INSERT so the suite can seed rows, but no route reaches it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from core.config import get_settings

# Named explicitly rather than left to search_path, so a connection that
# arrives without one still resolves to the right table.
SCHEMA = "audit"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            pool_size=10,
            # Recycles a connection Postgres closed under us rather than
            # handing a dead one to a request.
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, autoflush=False
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request.

    Nothing here commits, because nothing here writes. The rollback on the way
    out is what returns the connection cleanly when a query raises.
    """
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
