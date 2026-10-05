"""Settling an approved market, through the service layer. [3.4] #12, PR 2.

`POST /markets/{id}/settle` is the ledger's last step (ADR 0019, step 5). It
moves APPROVED to SETTLED, answers a market already SETTLED without writing,
refuses an APPROVED market that is not yet settleable `dispute_window_open`,
and refuses anything else `market_not_approved`. The window is judged on
Postgres's clock, read after the row lock. HTTP answers are in
unit_test/controller, the flip's audit entry in test_audit.py.

Refusals are matched on `code` and `status_code`, so these tests fix the
contract and not the error class names.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from core.errors import MarketError
from model.entities import Market, MarketStatus
from service import market_service
from service.audit import Actor
from unit_test.conftest import (
    approved_market,
    closed_market,
    database_now,
    draft_request,
    proposed_market,
    published_market,
    settled_market,
    time_until_settleable,
)
from unit_test.conftest import actor as _actor

# Read back as audit_svc: market_svc holds INSERT on this table and no SELECT.
_MARKED_SETTLED = text(
    "SELECT count(*) FROM audit.admin_actions "
    "WHERE actor_id = :actor AND action_type = 'market.marked_settled'"
)

_ONE_MICROSECOND = timedelta(microseconds=1)

# Long enough ago that any window has ended: only the status can refuse.
_STALE = timedelta(days=60)


async def _marked_settled(audit_reader: AsyncSession, *actors: Actor) -> int:
    total = 0
    for each in actors:
        total += (await audit_reader.execute(_MARKED_SETTLED, {"actor": each.id})).scalar_one()
    return total


async def _status_in(session: AsyncSession, market_id: uuid.UUID) -> MarketStatus:
    """The status as `session` itself sees it, before any rollback.

    Flushed first: the factory sets `autoflush=False`, and `expire_all` would
    otherwise discard a write the refusal left pending.
    """
    await session.flush()
    session.expire_all()
    return await session.scalar(select(Market.status).where(Market.id == market_id))


async def _committed_status(market_id: uuid.UUID) -> MarketStatus:
    """The status as a second session sees it: committed writes only."""
    async with get_session_factory()() as check:
        return await check.scalar(select(Market.status).where(Market.id == market_id))


async def _settleable_since(
    session: AsyncSession, creator: Actor, ago: timedelta, **approval: object
) -> Market:
    """An approved market whose settlement opened `ago` before the database's now.

    Approved in the past through `approve_outcome`'s clock, so the settle step
    can run on the real one. A negative `ago` puts the edge in the future.
    """
    approved_at = await database_now(session) - ago - time_until_settleable()
    return await approved_market(session, creator, approved_at=approved_at, **approval)


# --- the flip ----------------------------------------------------------------
async def test_an_approved_market_settles_from_the_edge(session: AsyncSession) -> None:
    """[3.4] #12: the settle step "moves APPROVED to SETTLED", and #12's
    boundary note: one "whose window ended five minutes ago or more settles".

    `now` is the edge itself, so this fails with the edge made exclusive. The
    status is read from a second session, so it fails without the commit.
    """
    opens = await database_now(session)
    market = await approved_market(
        session, _actor(), approved_at=opens - time_until_settleable()
    )

    settled = await market_service.settle(
        session, _actor(username="ihsan_b"), market.id, now=opens
    )

    assert settled.status is MarketStatus.SETTLED
    assert await _committed_status(market.id) is MarketStatus.SETTLED


async def test_settling_a_settled_market_answers_and_leaves_it_settled(
    session: AsyncSession,
) -> None:
    """[3.4] #12: the settle step "answers `200` without writing for a market
    already SETTLED". A repeat is how ADR 0019 repairs a failed last step, so
    it must not raise; `market_already_settled` here turns every repair into
    `503 settlement_unconfirmed`. The entry it must not write is test_audit.py's.
    """
    market = await settled_market(session, _actor())

    again = await market_service.settle(session, _actor(), market.id)

    assert again.status is MarketStatus.SETTLED
    assert await _committed_status(market.id) is MarketStatus.SETTLED


@pytest.mark.parametrize("who", ["proposer", "approver"])
async def test_the_proposer_and_the_approver_may_each_settle(
    session: AsyncSession, who: str
) -> None:
    """[3.4] #12: "Any administrator may call it, including the market's
    creator, proposer or approver." The two-person rule was spent at approval
    (ADR 0019). Fails with `_proposal_to_decide`'s identity rule copied here.
    """
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await _settleable_since(session, proposer, timedelta(0), approver=approver)
    settler = proposer if who == "proposer" else approver

    settled = await market_service.settle(session, settler, market.id)

    assert settled.status is MarketStatus.SETTLED


# --- what it refuses ---------------------------------------------------------
async def _draft(session: AsyncSession, owner: Actor) -> uuid.UUID:
    market, _, _ = await market_service.save(session, owner, draft_request())
    return market.id


async def _submitted(session: AsyncSession, owner: Actor) -> uuid.UUID:
    market, _, _ = await market_service.save(
        session, owner, draft_request(status="submitted")
    )
    return market.id


async def _open(session: AsyncSession, owner: Actor) -> uuid.UUID:
    return (await published_market(session, owner)).id


async def _closed(session: AsyncSession, owner: Actor) -> uuid.UUID:
    return (await closed_market(session, owner)).id


async def _pending(session: AsyncSession, owner: Actor) -> uuid.UUID:
    return (await proposed_market(session, owner)).id


_Build = Callable[[AsyncSession, Actor], Awaitable[uuid.UUID]]


@pytest.mark.parametrize(
    ("build", "status"),
    [
        (_draft, MarketStatus.DRAFT),
        (_submitted, MarketStatus.SUBMITTED),
        (_open, MarketStatus.OPEN),
        (_closed, MarketStatus.CLOSED),
        (_pending, MarketStatus.PENDING_RESOLUTION),
    ],
    ids=["another_admins_draft", "another_admins_submission", "open", "closed", "pending_resolution"],
)
async def test_each_status_short_of_approved_is_market_not_approved(
    session: AsyncSession, audit_reader: AsyncSession, build: _Build, status: MarketStatus
) -> None:
    """[3.4] #12: the settle step "refuses anything else `409
    market_not_approved`", and "The settle step checks in a fixed order":
    another administrator's draft or submitted market is found and refused,
    not 404.

    Every row carries a long-stale `approved_at`, so only the status can
    refuse it: fails with the status check removed, or with `get` in place
    of `get_any` (the drafts would be `market_not_found`). The status is
    re-read in the request's own session before anything rolls back.
    """
    owner, settler = _actor(), _actor(username="ihsan_b")
    market_id = await build(session, owner)
    await session.execute(
        update(Market)
        .where(Market.id == market_id)
        .values(approved_at=await database_now(session) - _STALE)
    )
    await session.commit()
    assert await _status_in(session, market_id) is status, "the builder must feed the named status"

    with pytest.raises(MarketError) as refused:
        await market_service.settle(session, settler, market_id)

    assert await _status_in(session, market_id) is status
    assert refused.value.code == "market_not_approved"
    assert refused.value.status_code == 409
    assert await _marked_settled(audit_reader, settler) == 0


async def test_an_unknown_market_is_not_found(session: AsyncSession) -> None:
    with pytest.raises(MarketError) as refused:
        await market_service.settle(session, _actor(), uuid.uuid4())

    assert refused.value.code == "market_not_found"
    assert refused.value.status_code == 404


@pytest.mark.parametrize(
    "when", ["an_hour_after_approval", "a_microsecond_before_the_edge"]
)
async def test_an_approved_market_not_yet_settleable_is_refused(
    session: AsyncSession, audit_reader: AsyncSession, when: str
) -> None:
    """[3.4] #12: an APPROVED market "that is not `settleable`" is `409
    dispute_window_open`, "so a direct call cannot settle mid-window", and
    the boundary note: a window that "ended less than five minutes ago is
    refused".

    The first case fails with the window check removed; the second, whose
    window ended five minutes less a microsecond ago, with the gap removed.
    """
    settler = _actor(username="ihsan_b")
    opens = await database_now(session)
    approved_at = opens - time_until_settleable()
    market = await approved_market(session, _actor(), approved_at=approved_at)
    if when == "an_hour_after_approval":
        now = approved_at + timedelta(hours=1)
    else:
        now = opens - _ONE_MICROSECOND

    with pytest.raises(MarketError) as refused:
        await market_service.settle(session, settler, market.id, now=now)

    assert await _status_in(session, market.id) is MarketStatus.APPROVED
    assert refused.value.code == "dispute_window_open"
    assert refused.value.status_code == 409
    assert await _marked_settled(audit_reader, settler) == 0


async def test_an_approved_market_with_no_approved_at_is_refused(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """"The settle step checks in a fixed order": "An APPROVED market with a
    null `approved_at` fails closed as `dispute_window_open`."

    Approved long ago, so only the null can refuse it: fails with the null
    read as settleable.
    """
    settler = _actor(username="ihsan_b")
    market = await _settleable_since(session, _actor(), _STALE)
    await session.execute(
        update(Market).where(Market.id == market.id).values(approved_at=None)
    )
    await session.commit()

    with pytest.raises(MarketError) as refused:
        await market_service.settle(session, settler, market.id)

    assert await _status_in(session, market.id) is MarketStatus.APPROVED
    assert refused.value.code == "dispute_window_open"
    assert refused.value.status_code == 409
    assert await _marked_settled(audit_reader, settler) == 0


# --- two requests at once ----------------------------------------------------
async def test_two_concurrent_settles_flip_the_market_once(
    clean_database, audit_reader: AsyncSession
) -> None:
    """The ledger's retry racing its first attempt: both answer, one flip in
    the log. ADR 0015: the status read that decides the write is locked.

    A third session holds the row while both settlers, each already
    connected, read it. Without `with_for_update` on the settle step's read,
    both read APPROVED, both UPDATEs queue behind the holder, and both commit
    a `market.marked_settled`: this goes red on every run. A status check
    alone cannot fail, since both writers write SETTLED.
    """
    factory = get_session_factory()
    first, second = _actor(), _actor(username="ihsan_b")

    async with factory() as setup:
        market_id = (await _settleable_since(setup, _actor(), timedelta(minutes=1))).id

    barrier = asyncio.Barrier(3)

    async def hold_the_row() -> None:
        async with factory() as own:
            await own.execute(
                select(Market.id).where(Market.id == market_id).with_for_update()
            )
            await barrier.wait()
            await asyncio.sleep(1)
            await own.rollback()

    async def settle(settler: Actor) -> MarketStatus:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            return (await market_service.settle(own, settler, market_id)).status

    _, first_answer, second_answer = await asyncio.gather(
        hold_the_row(), settle(first), settle(second)
    )

    assert first_answer is MarketStatus.SETTLED
    assert second_answer is MarketStatus.SETTLED
    assert await _committed_status(market_id) is MarketStatus.SETTLED
    flips = await _marked_settled(audit_reader, first, second)
    assert flips == 1, "the log must record one flip, not two"
