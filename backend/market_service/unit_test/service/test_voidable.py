"""`voidable` on the public detail, through the service layer. [BE] #221.

ADR 0025: "market_service decides this as `voidable`, the way it decides
`settleable`", on the stored status, against the request's clock. The rule's
edges are `unit_test/core/test_voidable.py`'s; these cases pin which status
reaches which half of it. Every case hands `get_published` its clock and
reads the flag off `PublicMarketOut`, which is what the ledger is sent.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import Market
from model.schemas import PublicMarketOut
from service import browsing
from service.audit import Actor
from unit_test.conftest import (
    actor,
    approved_market,
    closed_market,
    overdue_market,
    proposed_market,
    published_market,
    settled_market,
    voided_market,
)


async def _voidable(session: AsyncSession, market_id: uuid.UUID, now: datetime) -> bool:
    """The flag as the public detail carries it, read against `now`."""
    session.expire_all()
    market = await browsing.get_published(session, market_id, now=now)
    return PublicMarketOut.model_validate(market).voidable


async def _set_window_end(
    session: AsyncSession, market_id: uuid.UUID, ends_at: datetime | None
) -> None:
    await session.execute(
        update(Market).where(Market.id == market_id).values(dispute_window_ends_at=ends_at)
    )
    await session.commit()


async def _open(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await published_market(session, creator)).id


async def _past_close_unswept(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await overdue_market(session, creator)).id


async def _closed(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await closed_market(session, creator)).id


async def _pending(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await proposed_market(session, creator)).id


async def _approved(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await approved_market(session, creator)).id


async def _settled(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await settled_market(session, creator)).id


async def _voided(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await voided_market(session, creator)).id


_Build = Callable[[AsyncSession, Actor], Awaitable[uuid.UUID]]


@pytest.mark.parametrize(
    ("build", "window", "expected"),
    [
        # A window still open on every row that is not approved, so each
        # fails if the window rule runs whatever the status.
        (_open, "open", False),
        (_past_close_unswept, "open", False),
        (_settled, "open", False),
        # Long ended, so each fails if put through the window rule.
        (_closed, "ended", True),
        (_pending, "ended", True),
        (_voided, "ended", True),
        (_approved, "open", True),
        (_approved, "ended", False),
    ],
    ids=[
        "open",
        "past_close_unswept",
        "settled",
        "closed",
        "pending_resolution",
        "voided",
        "approved_inside_the_window",
        "approved_after_the_window",
    ],
)
async def test_voidable_by_stored_status(
    session: AsyncSession, build: _Build, window: str, expected: bool
) -> None:
    """ADR 0025's table: CLOSED, PENDING_RESOLUTION and VOIDED are voidable,
    APPROVED inside its window, and nothing else.

    `past_close_unswept` is "The status column, not the clock": the detail
    reads it `closed`, and it is not voidable until the sweep writes CLOSED.
    It fails if `voidable` reads the derived status.
    """
    now = datetime.now(UTC)
    market_id = await build(session, actor())
    if window == "open":
        await _set_window_end(session, market_id, now + timedelta(hours=1))
    else:
        await _set_window_end(session, market_id, now - timedelta(days=30))

    assert await _voidable(session, market_id, now) is expected
