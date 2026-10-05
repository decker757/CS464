"""`settleable` on the public detail, through the service layer. [3.4] #12, PR 1.

The ledger reads this flag rather than recomputing it ("Settlement waits for
`settleable`, which market_service derives from `approved_at`"). The rule,
from "`settleable` is a pure function in `core/`, inclusive at its edge, true
once settled, false without `approved_at`": a settled market always is; an
approved one is once `approved_at` is set and `now >= approved_at + window + 5
minutes`; nothing else is. Every case hands
`get_published` its clock and reads the flag off `PublicMarketOut`, which is
what the ledger is sent.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from model.entities import Market
from model.schemas import PublicMarketOut
from service import browsing
from service.audit import Actor
from unit_test.conftest import (
    actor,
    approved_market,
    closed_market,
    proposed_market,
    published_market,
    settled_market,
)

# "Settlement opens five minutes after send-back closes". A constant, not a
# setting, so the test names it too.
_GAP = timedelta(minutes=5)
_ONE_MICROSECOND = timedelta(microseconds=1)

# Long before any `now` these tests use: the approval send-back left behind.
_STALE = timedelta(days=30)


async def _settleable(
    session: AsyncSession, market_id: uuid.UUID, now: datetime
) -> bool:
    """The flag as the public detail carries it, read against `now`."""
    session.expire_all()
    market = await browsing.get_published(session, market_id, now=now)
    return PublicMarketOut.model_validate(market).settleable


async def _approved_at(session: AsyncSession, market_id: uuid.UUID) -> datetime:
    """`approved_at` as Postgres stored it, to the microsecond."""
    stored = await session.scalar(
        select(Market.approved_at).where(Market.id == market_id)
    )
    assert stored is not None, "the fixture must have approved this market"
    return stored


async def _set_approved_at(
    session: AsyncSession, market_id: uuid.UUID, approved_at: datetime | None
) -> None:
    await session.execute(
        update(Market).where(Market.id == market_id).values(approved_at=approved_at)
    )
    await session.commit()


def _edge(approved_at: datetime) -> datetime:
    """The first instant settlement is allowed, under the configured window."""
    window = timedelta(seconds=get_settings().dispute_window_seconds)
    return approved_at + window + _GAP


# --- the edge --------------------------------------------------------------
async def test_settleable_turns_true_exactly_five_minutes_after_the_window_ends(
    session: AsyncSession,
) -> None:
    """[3.4] #12: "true from five minutes after the dispute window ends";
    inclusive, because the boundary test settles one whose window ended "five
    minutes ago or more".

    Fails with `>` in place of `>=` (the exact case), and with the gap
    dropped (the case one microsecond early).
    """
    market_id = (await approved_market(session, actor())).id
    edge = _edge(await _approved_at(session, market_id))

    assert await _settleable(session, market_id, edge - _ONE_MICROSECOND) is False
    assert await _settleable(session, market_id, edge) is True


async def test_the_window_length_is_read_from_settings(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[3.3] #11's window is configurable; "The dispute window is
    `dispute_window_seconds`…".

    Patched on the cached settings to ten minutes, far from the 24-hour
    default. Fails if the window is hard-coded, or read once at import.
    """
    monkeypatch.setattr(get_settings(), "dispute_window_seconds", 600)
    market_id = (await approved_market(session, actor())).id
    edge = await _approved_at(session, market_id) + timedelta(seconds=600) + _GAP

    assert await _settleable(session, market_id, edge - _ONE_MICROSECOND) is False
    assert await _settleable(session, market_id, edge) is True


# --- by status -------------------------------------------------------------
async def _open_with_stale_approval(session: AsyncSession, creator: Actor) -> uuid.UUID:
    market_id = (await published_market(session, creator)).id
    await _set_approved_at(session, market_id, datetime.now(UTC) - _STALE)
    return market_id


async def _pending_with_stale_approval(
    session: AsyncSession, creator: Actor
) -> uuid.UUID:
    """Sent back, then proposed again ([3.3] #11): `approved_at` may survive."""
    market_id = (await proposed_market(session, creator)).id
    await _set_approved_at(session, market_id, datetime.now(UTC) - _STALE)
    return market_id


async def _closed_with_stale_approval(
    session: AsyncSession, creator: Actor
) -> uuid.UUID:
    """Sent back ([3.3] #11): APPROVED returns to CLOSED, and `approved_at` may
    survive. A plain CLOSED market has a null one and would not notice."""
    market_id = (await closed_market(session, creator)).id
    await _set_approved_at(session, market_id, datetime.now(UTC) - _STALE)
    return market_id


async def _approved(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await approved_market(session, creator)).id


async def _settled(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await settled_market(session, creator)).id


_Build = Callable[[AsyncSession, Actor], Awaitable[uuid.UUID]]


@pytest.mark.parametrize(
    ("build", "after_edge", "expected"),
    [
        # Each non-decided row carries an `approved_at`, or it would pass
        # with the status half of the rule removed.
        (_open_with_stale_approval, timedelta(hours=1), False),
        (_pending_with_stale_approval, timedelta(hours=1), False),
        (_closed_with_stale_approval, timedelta(hours=1), False),
        # The window has ended; the five-minute gap has not.
        (_approved, -timedelta(minutes=1), False),
        (_approved, timedelta(hours=1), True),
        (_settled, timedelta(hours=1), True),
    ],
    ids=[
        "open_with_stale_approved_at",
        "pending_with_stale_approved_at",
        "closed_with_stale_approved_at",
        "approved_inside_the_gap",
        "approved_after_the_gap",
        "settled",
    ],
)
async def test_settleable_by_status(
    session: AsyncSession, build: _Build, after_edge: timedelta, expected: bool
) -> None:
    """[3.4] #12: true from the edge, "stays true once the market is
    `settled`, and is false for every other status".

    `settled` fails with the settled half of the rule dropped; the stale
    rows fail with the approved half dropped.
    """
    market_id = await build(session, actor())
    edge = _edge(await _approved_at(session, market_id))

    assert await _settleable(session, market_id, edge + after_edge) is expected


@pytest.mark.parametrize(
    "keep_approved_at", [True, False], ids=["approved_a_moment_ago", "no_approved_at"]
)
async def test_a_settled_market_is_settleable_whatever_the_window(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, keep_approved_at: bool
) -> None:
    """[3.4] #12: `settleable` "stays true once the market is `settled`". A
    window raised to its 30-day ceiling, read a moment after approval, must not
    turn it back to false, and neither may a null `approved_at`.

    Fails with SETTLED put through the time rule, or the null check.
    """
    monkeypatch.setattr(get_settings(), "dispute_window_seconds", 2592000)
    market_id = (await settled_market(session, actor())).id
    if not keep_approved_at:
        await _set_approved_at(session, market_id, None)

    assert await _settleable(session, market_id, datetime.now(UTC)) is True


async def test_an_approved_market_with_no_approved_at_is_not_settleable(
    session: AsyncSession,
) -> None:
    """[3.4] #12: false "for a market with no `approved_at`". Approval always
    stamps it, so a null is damage, and the flag fails closed.

    A year ahead, so only the null can make it false.
    """
    market_id = (await approved_market(session, actor())).id
    await _set_approved_at(session, market_id, None)

    later = datetime.now(UTC) + timedelta(days=365)
    assert await _settleable(session, market_id, later) is False
