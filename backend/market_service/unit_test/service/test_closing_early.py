"""Closing a market early, driven through the service layer without HTTP. [2.3] #7.

The transition and the rules guarding it. Status codes and the error envelope
live in unit_test/controller; what reaches the audit log, which is where the
reason goes and the only place it exists, lives in unit_test/service/test_audit.py.

Two things this suite is really about, under the obvious ones.

**The gate derives from the clock rather than reading the status column**, so a
market whose closing time has just passed is refused even though `status` still
says `open`. `test_a_market_the_clock_already_closed_cannot_be_closed_by_hand`
is that rule, and it is worth more than it looks: accepting there would append
an entry claiming an administrator stopped trading that had already stopped.

**A market stopped by hand is a CLOSED market in every respect.** Nothing
downstream is allowed to care how it got there — a trade is refused, a save is
refused, the sweeper leaves it alone, and [3.1] #9 proposes an outcome on it.
The tests at the bottom are that claim, and they are the ones that would catch
an early close that wrote a state nothing else understands.
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

# The suite's actor factory and market builders. Aliased rather than imported
# under their own names because every helper below takes an `actor` argument,
# which would shadow the first.
from unit_test.conftest import CLOSE_REASON, closed_market, published_market
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
    """Asserted from the database rather than the in-memory object, so this
    fails if the transition is never actually committed."""
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
    """`close_time` is a term the administrator published and traders read.

    Rewriting it to "now" would make the market claim it always meant to stop
    here, and would contradict the `close_time` the `market.published` audit
    entry recorded. `closed_at` is the column that records what happened, and a
    `closed_at` earlier than `close_time` is exactly what an early close looks
    like from the outside.
    """
    actor = _actor()
    market = await _open_market(session, actor)
    close_time = market.close_time

    closed = await market_service.close_early(session, actor, market.id, _close())

    assert closed.close_time == close_time
    assert closed.closed_at < closed.close_time


async def test_closing_early_leaves_the_terms_alone(session: AsyncSession) -> None:
    """The request names a reason and nothing else.

    A market people have traded in cannot have its question or its outcomes
    changed on the way out — [3.1] #9 is about to settle against exactly these.
    """
    actor = _actor()
    market = await _open_market(session, actor, question="The question traders saw?")
    liquidity_b = market.liquidity_b

    closed = await market_service.close_early(session, actor, market.id, _close())

    assert closed.question == "The question traders saw?"
    assert [o.label for o in closed.outcomes] == ["Yes", "No"]
    assert closed.liquidity_b == liquidity_b


async def test_the_reason_is_not_stored_on_the_market(session: AsyncSession) -> None:
    """One fact, one place. ADR 0014.

    The reason lives in the audit log and nowhere else, so nothing here can
    drift from it and no reader has to work out which copy is authoritative.
    This is a guard against somebody adding a `closed_reason` column later and
    leaving two answers in the system.
    """
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
    """Same answer as a draft, and deliberately not the same as a closed one.

    A submitted market has not been published, so it may still go live; the
    error says so rather than implying the market is finished.
    """
    actor = _actor()
    market, _, _ = await market_service.save(session, actor, _request(status="submitted"))

    with pytest.raises(MarketNotOpen):
        await market_service.close_early(session, actor, market.id, _close())


async def test_an_already_closed_market_cannot_be_closed_again(
    session: AsyncSession,
) -> None:
    """Not an overwrite and not a second audit entry.

    The first close is the one that stopped trading and the only one that can
    have a true reason attached; a second would move `closed_at` forward to a
    moment when nothing happened.
    """
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(MarketClosed):
        await market_service.close_early(session, actor, market.id, _close())


async def test_a_market_the_clock_already_closed_cannot_be_closed_by_hand(
    session: AsyncSession,
) -> None:
    """The window ADR 0011 describes, and the reason this gate derives.

    `close_time` has passed and the sweeper has not run, so `status` still
    reads `open` and nothing can be traded. An early close accepted here would
    write an audit entry saying an administrator stopped a market that the
    clock stopped, at a `closed_at` that has nothing to do with when trading
    actually ended. The administrator gets `market_closed` — the same answer
    they would get one sweep later, which is the point of deriving it.
    """
    actor = _actor()
    market = await _open_market(session, actor)
    market.close_time = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    assert market.status is MarketStatus.OPEN

    with pytest.raises(MarketClosed):
        await market_service.close_early(session, actor, market.id, _close())


async def test_the_boundary_is_the_closing_time_itself(session: AsyncSession) -> None:
    """Closed *at* `close_time`, not a moment after.

    The same strictness `closing.is_open_for_trading` applies to a trade, which
    is what having one predicate rather than two comparisons is for.
    """
    actor = _actor()
    market = await _open_market(session, actor)

    with pytest.raises(MarketClosed):
        await market_service.close_early(
            session, actor, market.id, _close(), now=market.close_time
        )


async def test_a_market_awaiting_resolution_cannot_be_closed(
    session: AsyncSession,
) -> None:
    """A distinct error from `market_closed`, because the remedy differs: this
    administrator should be shown whose proposal is waiting."""
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
    """The one read in this service that is not scoped to the creator. ADR 0014.

    A market that is broken and can only be stopped by the administrator who
    made it is not oversight, which is the epic this story sits in. ADR 0007
    made the tier flat and the audit entry is what keeps it accountable — see
    test_audit.py, which asserts the entry names the administrator who actually
    reached in.
    """
    owner, overseer = _actor(username="ernest_t"), _actor(username="ihsan_b")
    market = await _open_market(session, owner)

    closed = await market_service.close_early(session, overseer, market.id, _close())

    assert closed.status is MarketStatus.CLOSED
    assert closed.creator_id == owner.id


async def test_the_creator_scoped_read_is_untouched(session: AsyncSession) -> None:
    """Widening the close must not have widened anything else.

    [1.1] #1's rule is that a draft is invisible to everyone but its creator,
    and `get_any` is one call away from `get`. If this ever passes for the
    second administrator, a draft has started leaking.
    """
    owner, other = _actor(), _actor()
    market, _, _ = await market_service.save(session, owner, _request())

    with pytest.raises(MarketNotFound):
        await market_service.get(session, other.id, market.id)


# --- the reason -----------------------------------------------------------
@pytest.mark.parametrize("reason", ["", "   ", "broken"])
async def test_a_market_is_not_closed_without_a_usable_reason(
    session: AsyncSession, reason: str
) -> None:
    """[2.3] #7's first criterion. The rules themselves are in
    unit_test/service/test_validation.py; this is that they are enforced here."""
    actor = _actor()
    market = await _open_market(session, actor)

    with pytest.raises(CloseIncomplete) as raised:
        await market_service.close_early(
            session, actor, market.id, _close(reason=reason)
        )

    assert [p.field for p in raised.value.problems] == ["reason"]


async def test_a_refused_close_leaves_the_market_open(session: AsyncSession) -> None:
    """Nothing is written. The market is still trading, which is what makes it
    safe for the modal to reopen with the reason the administrator typed."""
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
    """A market that cannot be closed at all is not told to fix its wording.

    The same ordering `propose_outcome` uses, and the same argument: being in
    the wrong state outranks what the request said.
    """
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(MarketClosed):
        await market_service.close_early(session, actor, market.id, _close(reason="x"))


# --- what a market closed by hand is --------------------------------------
async def test_a_market_closed_early_takes_no_more_trades(
    session: AsyncSession,
) -> None:
    """[2.3] #7's second criterion, asked of the predicate the trade path uses.

    Not `status == "closed"`. [T-2] #22 calls `is_open_for_trading`, so that is
    what has to answer False, and it does so the instant this commits rather
    than when any sweep next runs.
    """
    actor = _actor()
    market = await _open_market(session, actor)

    closed = await market_service.close_early(session, actor, market.id, _close())

    assert closing.is_open_for_trading(closed) is False


async def test_a_market_closed_early_is_not_listed_as_tradeable(
    session: AsyncSession,
) -> None:
    """The same answer from the WHERE clause half of the rule, which is what
    [BE][X] #62's browse query filters on."""
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
    """The form behind the close modal is still autosaving every three seconds.

    `_save_once` refuses every write to a closed market, so this needs no new
    branch — but it is the case CLAUDE.md names as the reason `MarketClosed` is
    reachable in ordinary use, so it is asserted rather than assumed.
    """
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
    """It is no longer `open`, so it is not in the sweep's working set at all —
    `ix_markets_due_close` is partial on exactly that. What matters is that
    `closed_at` keeps saying when the administrator stopped it, rather than
    being moved to when its closing time arrived.
    """
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
    """The point of the whole ticket, and the thing that would break if the
    early close wrote a state of its own.

    A market stopped by hand is CLOSED in exactly the sense [3.1] #9 requires,
    so it settles down the same path as one that ran to its closing time. A
    broken market that could be stopped but never resolved would leave every
    position in it frozen for good.
    """
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
    out while it waited. #97.

    The same defect as `publish`'s, and it lands somewhere worse. The gate
    here derives from the clock precisely so that an administrator cannot stop
    trading that the clock has already stopped — ADR 0014's reason for
    deriving rather than reading the status column. Sampling `now` before an
    unbounded lock wait reintroduces the entry that rule exists to prevent, by
    a different route: not a stale status column, but a stale clock.

    What would be written is the part that matters. `closed_at` would be set
    from the pre-wait timestamp and the audit entry would name an
    administrator as the one who stopped a market the clock closed on its own,
    which is the one thing `market.closed_early` is supposed to mean.
    """
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

    # Still OPEN in the column — the sweeper writes CLOSED, and no
    # `closed_at` means no administrator is on record as having done it.
    assert stored.status is MarketStatus.OPEN
    assert stored.closed_at is None
