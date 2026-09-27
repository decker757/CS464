"""Closing a market early, driven through the service layer without HTTP. [2.3] #7.

The gate derives from the clock, not the status column, and a market stopped by
hand is CLOSED in every respect downstream; the tests at the bottom hold that.
ADR 0014. HTTP answers are in unit_test/controller, the audit entry (the
reason's only copy) in test_audit.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    CloseIncomplete,
    MarketClosed,
    MarketNotFound,
    MarketNotOpen,
    MarketPendingResolution,
)
from model.entities import Market, MarketStatus
from service import closing, market_service
from service.audit import Actor

# Aliased so the names do not shadow an `actor` argument.
from unit_test.conftest import (
    CLOSE_REASON,
    closed_market,
    overdue_market,
    published_market,
)
from unit_test.conftest import actor as _actor
from unit_test.conftest import close_request as _close
from unit_test.conftest import draft_request as _request
from unit_test.conftest import proposal_request as _proposal


async def _open_market(session: AsyncSession, actor: Actor, **overrides: object) -> Market:
    """A market traders can reach, which is the only kind that can be stopped."""
    return await published_market(session, actor, **overrides)


# --- the transition -------------------------------------------------------
async def test_an_open_market_closes(session: AsyncSession) -> None:
    """[2.3] #7's second criterion: the status moves to CLOSED."""
    actor = _actor()
    market = await _open_market(session, actor)

    closed = await market_service.close_early(session, actor, market.id, _close())

    assert closed.status is MarketStatus.CLOSED
    assert closed.closed_at is not None


async def test_the_close_survives_a_reload(session: AsyncSession) -> None:
    """Read from the database, so this fails if the transition is never committed."""
    actor = _actor()
    market = await _open_market(session, actor)

    await market_service.close_early(session, actor, market.id, _close())

    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.CLOSED
    assert stored.closed_at is not None


async def test_closing_early_leaves_the_closing_time_alone(
    session: AsyncSession,
) -> None:
    """`close_time` is a published term; a `closed_at` before it marks the early close. ADR 0014."""
    actor = _actor()
    market = await _open_market(session, actor)
    close_time = market.close_time

    closed = await market_service.close_early(session, actor, market.id, _close())

    assert closed.close_time == close_time
    assert closed.closed_at < closed.close_time


async def test_closing_early_leaves_the_terms_alone(session: AsyncSession) -> None:
    """The request names a reason and nothing else; the terms are about to be settled."""
    actor = _actor()
    market = await _open_market(session, actor, question="The question traders saw?")
    liquidity_b = market.liquidity_b

    closed = await market_service.close_early(session, actor, market.id, _close())

    assert closed.question == "The question traders saw?"
    assert [o.label for o in closed.outcomes] == ["Yes", "No"]
    assert closed.liquidity_b == liquidity_b


async def test_the_reason_is_not_stored_on_the_market(session: AsyncSession) -> None:
    """Guard: the reason lives in the audit log only, not a `closed_reason` column. ADR 0014."""
    actor = _actor()
    market = await _open_market(session, actor)

    closed = await market_service.close_early(session, actor, market.id, _close())

    assert not any(
        CLOSE_REASON == getattr(closed, column.key, None)
        for column in Market.__table__.columns
    )


# --- which markets may be closed ------------------------------------------
async def test_a_draft_cannot_be_closed(session: AsyncSession) -> None:
    """There is nothing to stop. Nobody has ever been able to trade it."""
    actor = _actor()
    market, _, _ = await market_service.save(session, actor, _request())

    with pytest.raises(MarketNotOpen):
        await market_service.close_early(session, actor, market.id, _close())


async def test_a_submitted_market_cannot_be_closed(session: AsyncSession) -> None:
    """Same answer as a draft, not a closed one: it may still go live."""
    actor = _actor()
    market, _, _ = await market_service.save(session, actor, _request(status="submitted"))

    with pytest.raises(MarketNotOpen):
        await market_service.close_early(session, actor, market.id, _close())


async def test_an_already_closed_market_cannot_be_closed_again(
    session: AsyncSession,
) -> None:
    """Not an overwrite of `closed_at`, and not a second audit entry."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(MarketClosed):
        await market_service.close_early(session, actor, market.id, _close())


async def test_a_market_the_clock_already_closed_cannot_be_closed_by_hand(
    session: AsyncSession,
) -> None:
    """The sweep has not run and `status` reads `open`, but the clock has stopped
    trading: `market_closed`, as one sweep later. ADR 0014."""
    actor = _actor()
    market = await overdue_market(session, actor)

    assert market.status is MarketStatus.OPEN

    with pytest.raises(MarketClosed):
        await market_service.close_early(session, actor, market.id, _close())


async def test_the_boundary_is_the_closing_time_itself(session: AsyncSession) -> None:
    """Closed *at* `close_time`, the same strictness a trade gets."""
    actor = _actor()
    market = await _open_market(session, actor)

    with pytest.raises(MarketClosed):
        await market_service.close_early(
            session, actor, market.id, _close(), now=market.close_time
        )


async def test_a_market_awaiting_resolution_cannot_be_closed(
    session: AsyncSession,
) -> None:
    """Not `market_closed`: this administrator should be shown the proposal."""
    actor = _actor()
    market = await closed_market(session, actor)
    await market_service.propose_outcome(
        session, actor, market.id, _proposal(market.outcomes[0].id)
    )

    with pytest.raises(MarketPendingResolution):
        await market_service.close_early(session, actor, market.id, _close())


async def test_an_unknown_market_is_not_found(session: AsyncSession) -> None:
    actor = _actor()

    with pytest.raises(MarketNotFound):
        await market_service.close_early(session, actor, uuid.uuid4(), _close())


# --- who may close --------------------------------------------------------
async def test_any_administrator_may_close_another_ones_market(
    session: AsyncSession,
) -> None:
    """Any admin may stop any market; the audit entry names who. ADR 0014."""
    owner, overseer = _actor(username="ernest_t"), _actor(username="ihsan_b")
    market = await _open_market(session, owner)

    closed = await market_service.close_early(session, overseer, market.id, _close())

    assert closed.status is MarketStatus.CLOSED
    assert closed.creator_id == owner.id


# --- the reason -----------------------------------------------------------
@pytest.mark.parametrize("reason", ["", "   ", "broken"])
async def test_a_market_is_not_closed_without_a_usable_reason(
    session: AsyncSession, reason: str
) -> None:
    """[2.3] #7's first criterion, enforced; the rules are in test_validation.py."""
    actor = _actor()
    market = await _open_market(session, actor)

    with pytest.raises(CloseIncomplete) as raised:
        await market_service.close_early(
            session, actor, market.id, _close(reason=reason)
        )

    assert [p.field for p in raised.value.problems] == ["reason"]


async def test_a_refused_close_leaves_the_market_open(session: AsyncSession) -> None:
    """Nothing is written; the market is still trading."""
    actor = _actor()
    market = await _open_market(session, actor)

    with pytest.raises(CloseIncomplete):
        await market_service.close_early(session, actor, market.id, _close(reason="x"))

    await session.rollback()
    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.OPEN
    assert stored.closed_at is None


async def test_the_state_is_checked_before_the_reason(session: AsyncSession) -> None:
    """A market that cannot be closed at all is not told to fix its wording."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(MarketClosed):
        await market_service.close_early(session, actor, market.id, _close(reason="x"))


# --- what a market closed by hand is --------------------------------------
async def test_a_market_closed_early_takes_no_more_trades(
    session: AsyncSession,
) -> None:
    """[2.3] #7's second criterion, asked of the predicate the trade path uses."""
    actor = _actor()
    market = await _open_market(session, actor)

    closed = await market_service.close_early(session, actor, market.id, _close())

    assert closing.is_open_for_trading(closed) is False


async def test_a_market_closed_early_is_not_listed_as_tradeable(
    session: AsyncSession,
) -> None:
    """The same answer from the WHERE-clause form the browse query uses."""
    actor = _actor()
    market = await _open_market(session, actor)
    await market_service.close_early(session, actor, market.id, _close())

    tradeable = (
        await session.execute(select(Market.id).where(closing.open_for_trading()))
    ).scalars().all()

    assert market.id not in tradeable


async def test_an_autosave_to_a_market_closed_early_is_refused(
    session: AsyncSession,
) -> None:
    """The form behind the close modal is still autosaving."""
    actor = _actor()
    market = await _open_market(session, actor)
    draft_key = market.draft_key

    await market_service.close_early(session, actor, market.id, _close())

    with pytest.raises(MarketClosed):
        await market_service.save(
            session, actor, _request(draft_key=draft_key, question="Edited?")
        )


async def test_the_sweeper_leaves_a_market_closed_early_alone(
    session: AsyncSession,
) -> None:
    """`closed_at` keeps saying when the administrator stopped it."""
    actor = _actor()
    market = await _open_market(session, actor)
    closed = await market_service.close_early(session, actor, market.id, _close())
    closed_at = closed.closed_at

    closed.close_time = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    assert await closing.close_due_markets(session, limit=10) == []

    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.closed_at == closed_at


async def test_an_outcome_can_be_proposed_for_a_market_closed_early(
    session: AsyncSession,
) -> None:
    """A market stopped by hand settles down the same path as any closed one."""
    actor = _actor()
    market = await _open_market(session, actor)
    await market_service.close_early(session, actor, market.id, _close())

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(market.outcomes[0].id)
    )

    assert proposed.status is MarketStatus.PENDING_RESOLUTION


async def test_closing_early_reads_the_clock_after_it_has_the_lock(
    session: AsyncSession,
) -> None:
    """An early close that queued for the row lock is refused if the clock ran
    out while it waited, or the log would credit an admin with the clock's
    close. #97, ADR 0014."""
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    factory = get_session_factory()
    actor = _actor()

    async with factory() as setup:
        market = await _open_market(
            setup, actor, close_time=datetime.now(UTC) + timedelta(seconds=1.5)
        )
        market_id = market.id

    barrier = asyncio.Barrier(2)

    async def hold_the_row() -> None:
        async with factory() as own:
            await own.execute(select(Market).where(Market.id == market_id).with_for_update())
            await barrier.wait()
            await asyncio.sleep(3)
            await own.rollback()

    async def close_it() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await market_service.close_early(own, actor, market_id, _close())
            except MarketClosed:
                return "refused"
            return "closed"

    _, outcome = await asyncio.gather(hold_the_row(), close_it())

    assert outcome == "refused", "the clock closed this market while the request waited"

    async with factory() as check:
        stored = (
            await check.execute(select(Market).where(Market.id == market_id))
        ).scalar_one()

    # Still OPEN in the column, and no `closed_at`: no admin is on record.
    assert stored.status is MarketStatus.OPEN
    assert stored.closed_at is None
