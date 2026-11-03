"""The close sweep's two gauges: how late it woke, and when it last succeeded.
#599

The registry is process-wide, so each assertion reads it after the sweep and
the test sets the state it expects first.
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from prometheus_client import REGISTRY
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database import get_session_factory
from model.entities import MarketStatus
from service import closing, market_service
from service.sweeper import sweep_once
from unit_test.conftest import actor, overdue_market

LAG = "market_close_sweep_lag_seconds"
LAST_SUCCESS = "market_close_sweep_last_success_timestamp_seconds"


def _gauge(name: str) -> float:
    value = REGISTRY.get_sample_value(name)
    assert value is not None, f"{name} is not registered"
    return value


async def test_a_sweep_reports_how_overdue_the_oldest_open_market_was(session) -> None:
    admin = actor()
    market = await overdue_market(session, admin, overdue_by=timedelta(seconds=120))
    market_id = market.id

    await sweep_once(get_session_factory(), batch_limit=100)

    assert 119 <= _gauge(LAG) <= 150
    session.expire_all()
    closed = await market_service.get(session, admin.id, market_id)
    assert closed.status is MarketStatus.CLOSED


async def test_a_sweep_with_nothing_due_reports_no_lag(session) -> None:
    # Leave a stale non-zero reading behind, as a previous sweep would have.
    await overdue_market(session, actor(), overdue_by=timedelta(seconds=60))
    await sweep_once(get_session_factory(), batch_limit=100)
    assert _gauge(LAG) > 0

    await sweep_once(get_session_factory(), batch_limit=100)

    assert _gauge(LAG) == 0.0


async def test_a_successful_sweep_records_when_it_finished(session) -> None:
    before = time.time()

    await sweep_once(get_session_factory(), batch_limit=100)

    assert before <= _gauge(LAST_SUCCESS) <= time.time()


async def test_a_failed_sweep_leaves_the_last_success_where_it_was(
    clean_database,
) -> None:
    """A dead sweeper must show as growing lag, so a failure never touches it."""
    await sweep_once(get_session_factory(), batch_limit=100)
    recorded = _gauge(LAST_SUCCESS)
    unreachable = create_async_engine("postgresql+asyncpg://127.0.0.1:1/none")

    try:
        with pytest.raises((SQLAlchemyError, OSError)):
            await sweep_once(
                async_sessionmaker(unreachable, expire_on_commit=False),
                batch_limit=100,
            )
    finally:
        await unreachable.dispose()

    assert _gauge(LAST_SUCCESS) == recorded


async def test_a_sweep_that_fails_while_closing_leaves_the_last_success_alone(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lag read worked and the drain did not: still not a success."""
    await sweep_once(get_session_factory(), batch_limit=100)
    recorded = _gauge(LAST_SUCCESS)

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("the update failed")

    monkeypatch.setattr(closing, "close_due_markets", refuse)

    with pytest.raises(SQLAlchemyError):
        await sweep_once(get_session_factory(), batch_limit=100)

    assert _gauge(LAST_SUCCESS) == recorded
