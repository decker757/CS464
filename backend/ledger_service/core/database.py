"""Async engine, session factory, and the FastAPI session dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction

from core.config import get_settings

# Created and granted in sql/02-schemas.sql. Named explicitly rather than left
# to search_path, so a connection without one cannot create these tables in
# public.
SCHEMA = "ledger"

# A `session.info` flag, set by a successful flush and cleared when the
# outermost transaction ends. A flushed row leaves `session.new`, but a
# commit still lands it.
_FLUSHED_KEY = "ledger.flushed_uncommitted"


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
    """One session and one transaction per request.

    Committing is the service layer's job. This rolls back a request that
    raises, so half a double-entry transaction is never left behind.
    """
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@event.listens_for(Session, "after_flush")
def _remember_the_flush(session: Session, flush_context: object) -> None:
    session.info[_FLUSHED_KEY] = True


@event.listens_for(Session, "after_transaction_end")
def _forget_the_flush(session: Session, transaction: SessionTransaction) -> None:
    # Only the outermost transaction: the inner one every flush opens, and a
    # released SAVEPOINT, both end with their rows still uncommitted, and it
    # is the commit or rollback that settles them.
    if transaction.parent is None:
        session.info.pop(_FLUSHED_KEY, None)


def has_pending_writes(session: AsyncSession) -> bool:
    """True if the session holds writes its transaction has not committed:
    adds, changes or deletes not yet flushed, or any flush since it began.

    Errs toward True: a flush later rolled back to a SAVEPOINT still counts
    until the transaction ends, which fails closed where this is the alarm
    (`PendingWritesOnReplay`). Sees ORM writes only, not a Core `insert()`
    executed directly.
    """
    return bool(
        session.new
        or session.dirty
        or session.deleted
        or session.info.get(_FLUSHED_KEY)
    )


async def create_all() -> None:
    """Create this service's tables, and with them the append-only trigger on
    `ledger.entries` (an `after_create` event, ADR 0009).

    Callers must import `model.entities` first; core must not reach up into
    `model` to do it. Becomes Alembic before a column changes on a table
    holding data worth keeping.
    """
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
