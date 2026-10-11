"""`settleable` on the public detail, through the service layer. [3.4] #12, PR 1.

The ledger reads this flag rather than recomputing it ("Settlement waits for
`settleable`, which market_service derives from `approved_at`", as amended by
[3.3] #11). The rule, from "`settleable` is a pure function in `core/`,
inclusive at its edge, true once settled, false without `approved_at`" and its
amendment: a settled market always is; an approved one is once
`dispute_window_ends_at`, the stored end, is set and `now >= that end + 5
minutes`; nothing else is. Approval stores the end as `approved_at` plus the
window as it was then (D2, ADR 0023). Every case hands
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
    settleable_edge,
    settled_market,
)

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


async def _set_window_end(
    session: AsyncSession, market_id: uuid.UUID, ends_at: datetime | None
) -> None:
    await session.execute(
        update(Market)
        .where(Market.id == market_id)
        .values(dispute_window_ends_at=ends_at)
    )
    await session.commit()


async def _set_stale_approval(session: AsyncSession, market_id: uuid.UUID) -> None:
    """An approval and its stored window end, both long over, left on the row.

    Both columns, so only the status half of the rule can make the flag false.
    """
    approved_at = datetime.now(UTC) - _STALE
    await session.execute(
        update(Market)
        .where(Market.id == market_id)
        .values(
            approved_at=approved_at,
            dispute_window_ends_at=approved_at
            + timedelta(seconds=get_settings().dispute_window_seconds),
        )
    )
    await session.commit()


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
    edge = settleable_edge(await _approved_at(session, market_id))

    assert await _settleable(session, market_id, edge - _ONE_MICROSECOND) is False
    assert await _settleable(session, market_id, edge) is True


async def test_the_window_length_is_read_from_settings(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[3.3] #11's window is configurable; "The dispute window is
    `dispute_window_seconds`…".

    Patched on the cached settings to ten minutes, far from the 24-hour
    default, before the approval that reads it. Fails if the window is
    hard-coded, or read once at import.
    """
    monkeypatch.setattr(get_settings(), "dispute_window_seconds", 600)
    market_id = (await approved_market(session, actor())).id
    edge = settleable_edge(await _approved_at(session, market_id))

    assert await _settleable(session, market_id, edge - _ONE_MICROSECOND) is False
    assert await _settleable(session, market_id, edge) is True


async def test_changing_the_window_setting_does_not_move_a_running_window(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[3.3] #11, D2: the end is stored at approval, so a setting changed
    afterwards affects only later approvals. ADR 0023.

    Approved under 24 hours, then the setting drops to ten minutes; fifteen
    minutes on, the old rule (`approved_at + window + 5 minutes`, recomputed)
    says settleable. Fails if `settleable` reads the setting again.
    """
    monkeypatch.setattr(get_settings(), "dispute_window_seconds", 86400)
    market_id = (await approved_market(session, actor())).id
    approved_at = await _approved_at(session, market_id)

    monkeypatch.setattr(get_settings(), "dispute_window_seconds", 600)

    later = approved_at + timedelta(minutes=15)
    assert await _settleable(session, market_id, later) is False


# --- by status -------------------------------------------------------------
async def _open_with_stale_approval(session: AsyncSession, creator: Actor) -> uuid.UUID:
    market_id = (await published_market(session, creator)).id
    await _set_stale_approval(session, market_id)
    return market_id


async def _pending_with_stale_approval(
    session: AsyncSession, creator: Actor
) -> uuid.UUID:
    """Sent back, then proposed again ([3.3] #11), with an approval left over."""
    market_id = (await proposed_market(session, creator)).id
    await _set_stale_approval(session, market_id)
    return market_id


async def _closed_with_stale_approval(
    session: AsyncSession, creator: Actor
) -> uuid.UUID:
    """Sent back ([3.3] #11), with an approval left over. A plain CLOSED market
    has none and would not notice."""
    market_id = (await closed_market(session, creator)).id
    await _set_stale_approval(session, market_id)
    return market_id


async def _approved(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await approved_market(session, creator)).id


async def _settled(session: AsyncSession, creator: Actor) -> uuid.UUID:
    return (await settled_market(session, creator)).id


_Build = Callable[[AsyncSession, Actor], Awaitable[uuid.UUID]]


@pytest.mark.parametrize(
    ("build", "after_edge", "expected"),
    [
        # Each non-decided row carries an approval and a window end, or it
        # would pass with the status half of the rule removed.
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
    edge = settleable_edge(await _approved_at(session, market_id))

    assert await _settleable(session, market_id, edge + after_edge) is expected


async def test_a_settled_market_is_settleable_whatever_the_window(
    session: AsyncSession,
) -> None:
    """[3.4] #12: `settleable` "stays true once the market is `settled`", even
    with no stored window end.

    Fails with SETTLED put through the time rule, or the null check.
    """
    market_id = (await settled_market(session, actor())).id
    await _set_window_end(session, market_id, None)

    assert await _settleable(session, market_id, datetime.now(UTC)) is True


async def test_an_approved_market_with_no_window_end_is_not_settleable(
    session: AsyncSession,
) -> None:
    """[3.4] #12, as amended by [3.3] #11: false for an approved market with no
    `dispute_window_ends_at`. Approval always stores it, so a null is damage,
    and the flag fails closed.

    A year ahead, so only the null can make it false.
    """
    market_id = (await approved_market(session, actor())).id
    await _set_window_end(session, market_id, None)

    later = datetime.now(UTC) + timedelta(days=365)
    assert await _settleable(session, market_id, later) is False
