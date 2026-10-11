"""Sending an approved outcome back, through the service layer. [3.3] #11.

While the stored dispute window is open, any administrator may send an
approved result back: the market returns to CLOSED with the proposal and the
approval cleared, and the outcome is redone through propose and approve. The
checks come in a fixed order (state, clock, window, which approval, reason),
the window's edge is exclusive and judged on Postgres's clock after the row
lock, and the audit entry is the only copy of what was sent back. ADR 0023.
HTTP answers are in unit_test/controller.

Refusals are matched on `code` and `status_code`, so these tests fix the
contract and not the error class names.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.database import get_session_factory
from core.errors import MarketError
from model.entities import Market, MarketStatus
from model.schemas import OutcomeApprovalRequest, OutcomeSendBackRequest, PublicMarketOut
from service import browsing, market_service
from service.audit import Actor
from unit_test.conftest import (
    EVIDENCE_NOTE,
    EVIDENCE_URL,
    SEND_BACK_REASON,
    approved_market,
    closed_market,
    database_now,
    draft_request,
    proposed_before_ids,
    proposed_market,
    published_market,
)
from unit_test.conftest import actor as _actor
from unit_test.conftest import approval_request as _approve
from unit_test.conftest import proposal_request as _proposal
from unit_test.conftest import send_back_request as _send_back

# Read back as audit_svc: market_svc holds INSERT on this table and no SELECT.
_ENTRIES = text(
    "SELECT * FROM audit.admin_actions "
    "WHERE actor_id = :actor AND action_type = :action"
)

_SENT_BACK = "market.outcome_sent_back"

# The seven proposal columns, the three approval columns and the window's end.
_CLEARED_COLUMNS = (
    "proposal_id",
    "proposed_outcome_id",
    "proposed_by_id",
    "proposed_by_username",
    "proposed_at",
    "proposal_evidence_url",
    "proposal_evidence_note",
    "approved_by_id",
    "approved_by_username",
    "approved_at",
    "dispute_window_ends_at",
)

_ONE_MICROSECOND = timedelta(microseconds=1)

# "Settlement opens five minutes after send-back closes" (ADR 0019), named
# here rather than imported, as conftest.py does.
_SETTLEMENT_GAP = timedelta(minutes=5)


async def _entries(audit_reader: AsyncSession, actor: Actor, action: str) -> list:
    rows = await audit_reader.execute(_ENTRIES, {"actor": actor.id, "action": action})
    return list(rows.mappings())


async def _stored(session: AsyncSession, market_id: uuid.UUID) -> Market:
    """The market re-read from Postgres in the caller's session."""
    session.expire_all()
    return (
        await session.execute(select(Market).where(Market.id == market_id))
    ).scalar_one()


async def _committed_status(market_id: uuid.UUID) -> MarketStatus:
    """The status as a second session sees it: committed writes only."""
    async with get_session_factory()() as check:
        return await check.scalar(select(Market.status).where(Market.id == market_id))


async def _set_window_end(
    session: AsyncSession, market_id: uuid.UUID, ends_at: datetime | None
) -> None:
    await session.execute(
        update(Market)
        .where(Market.id == market_id)
        .values(dispute_window_ends_at=ends_at)
    )
    await session.commit()


# --- the transition [3.3] #11 ------------------------------------------------
async def test_an_approved_market_inside_its_window_is_sent_back_to_closed(
    session: AsyncSession,
) -> None:
    """AC: "the market returns to CLOSED". Read from a second session, so this
    fails without the commit."""
    market = await approved_market(session, _actor())

    sent = await market_service.send_back_outcome(
        session, _actor(username="carol_a"), market.id, _send_back(market.proposal_id)
    )

    assert sent.status is MarketStatus.CLOSED
    assert await _committed_status(market.id) is MarketStatus.CLOSED


async def test_a_send_back_clears_every_proposal_and_approval_column(
    session: AsyncSession,
) -> None:
    """The eleven columns go, so the market is exactly a rejected one: CLOSED
    with no proposal, no approver and no window. ADR 0023."""
    market = await approved_market(session, _actor())
    market_id = market.id

    await market_service.send_back_outcome(
        session, _actor(), market_id, _send_back(market.proposal_id)
    )

    stored = await _stored(session, market_id)
    for column in _CLEARED_COLUMNS:
        assert getattr(stored, column) is None, column


async def test_a_send_back_leaves_closed_at_alone(session: AsyncSession) -> None:
    """Trading stopped when it stopped, as for a rejection. ADR 0023."""
    market = await approved_market(session, _actor())
    market_id, closed_at = market.id, market.closed_at
    assert closed_at is not None

    await market_service.send_back_outcome(
        session, _actor(), market_id, _send_back(market.proposal_id)
    )

    assert (await _stored(session, market_id)).closed_at == closed_at


async def test_a_sent_back_market_can_be_proposed_and_approved_again_with_a_fresh_window(
    session: AsyncSession,
) -> None:
    """AC: "the decision is redone through propose → approve, and that starts
    a fresh window". The re-approval stamps its own `approved_at` and stores
    its own end, so the result is not settleable yet."""
    creator, approver = _actor(), _actor(username="ihsan_b")
    market = await approved_market(session, creator, approver=approver)
    market_id, first_proposal = market.id, market.proposal_id
    first_approved_at, winner = market.approved_at, market.outcomes[1].id

    await market_service.send_back_outcome(
        session, _actor(username="carol_a"), market_id, _send_back(first_proposal)
    )
    reproposed = await market_service.propose_outcome(
        session, creator, market_id, _proposal(winner)
    )
    reapproved = await market_service.approve_outcome(
        session, _actor(username="dave_d"), market_id, _approve(reproposed.proposal_id)
    )

    window = timedelta(seconds=get_settings().dispute_window_seconds)
    assert reapproved.status is MarketStatus.APPROVED
    assert reapproved.proposal_id not in (None, first_proposal)
    assert reapproved.approved_at > first_approved_at
    assert reapproved.dispute_window_ends_at == reapproved.approved_at + window

    session.expire_all()
    detail = await browsing.get_published(session, market_id)
    assert PublicMarketOut.model_validate(detail).settleable is False


# --- who [3.3] #11, D1 -------------------------------------------------------
@pytest.mark.parametrize("who", ["proposer", "approver", "third_admin"])
async def test_any_administrator_may_send_back(session: AsyncSession, who: str) -> None:
    """D1: any administrator, the proposer and the approver included. Fails if
    an identity check is added, such as `_proposal_to_decide`'s."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await approved_market(session, proposer, approver=approver)
    senders = {"proposer": proposer, "approver": approver, "third_admin": _actor()}

    sent = await market_service.send_back_outcome(
        session, senders[who], market.id, _send_back(market.proposal_id)
    )

    assert sent.status is MarketStatus.CLOSED


# --- the window [3.3] #11 ----------------------------------------------------
async def test_send_back_is_refused_from_the_moment_the_window_ends(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """AC: "After the window, send-back is refused", and "only while
    `now < window_ends_at`". The end is set an hour after approval, which no
    window setting gives, so this also proves send-back reads the stored end.

    Fails with the edge made `<=`, with the check dropped, or with the end
    computed from the setting.
    """
    sender = _actor()
    market = await approved_market(session, _actor())
    market_id, proposal_id = market.id, market.proposal_id
    ends_at = market.approved_at + timedelta(hours=1)
    await _set_window_end(session, market_id, ends_at)

    with pytest.raises(MarketError) as refused:
        await market_service.send_back_outcome(
            session, sender, market_id, _send_back(proposal_id), now=ends_at
        )
    await session.rollback()

    assert refused.value.code == "dispute_window_closed"
    assert refused.value.status_code == 409
    assert (await _stored(session, market_id)).status is MarketStatus.APPROVED
    assert await _entries(audit_reader, sender, _SENT_BACK) == []

    sent = await market_service.send_back_outcome(
        session, sender, market_id, _send_back(proposal_id),
        now=ends_at - _ONE_MICROSECOND,
    )
    assert sent.status is MarketStatus.CLOSED


async def test_an_approved_market_with_no_window_end_cannot_be_sent_back(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """A null end is damage, and fails closed as `settleable` does. Read at
    the approval instant, inside any window, so only the null can refuse."""
    sender = _actor()
    market = await approved_market(session, _actor())
    market_id, proposal_id, approved_at = market.id, market.proposal_id, market.approved_at
    await _set_window_end(session, market_id, None)

    with pytest.raises(MarketError) as refused:
        await market_service.send_back_outcome(
            session, sender, market_id, _send_back(proposal_id), now=approved_at
        )

    assert refused.value.code == "dispute_window_closed"
    assert await _entries(audit_reader, sender, _SENT_BACK) == []


async def test_a_send_back_that_queued_past_the_edge_is_refused(
    clean_database, audit_reader: AsyncSession
) -> None:
    """AC: "checked after the row lock, on `clock_timestamp()`". A send-back
    that arrived inside the window and got the row after it is refused: the
    clock is read once it holds the lock. ADR 0015.

    The send-back connects and starts with the window open; a third session
    holds the row until the end has passed. Fails with the clock read before
    the lock, or through `func.now()` (the transaction's start, #138).
    """
    factory = get_session_factory()
    sender = _actor()

    async with factory() as setup:
        market = await approved_market(setup, _actor())
        market_id, proposal_id = market.id, market.proposal_id
        await _set_window_end(
            setup, market_id, await database_now(setup) + timedelta(seconds=1)
        )

    barrier = asyncio.Barrier(2)

    async def hold_the_row() -> None:
        async with factory() as own:
            await own.execute(
                select(Market.id).where(Market.id == market_id).with_for_update()
            )
            await barrier.wait()
            await asyncio.sleep(2)
            await own.rollback()

    async def send_back() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await market_service.send_back_outcome(
                    own, sender, market_id, _send_back(proposal_id)
                )
            except MarketError as refused:
                return refused.code
            return "sent back"

    _, answer = await asyncio.gather(hold_the_row(), send_back())

    assert answer == "dispute_window_closed"
    assert await _committed_status(market_id) is MarketStatus.APPROVED
    assert await _entries(audit_reader, sender, _SENT_BACK) == []


# --- state -------------------------------------------------------------------
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


async def _unknown(session: AsyncSession, owner: Actor) -> uuid.UUID:
    return uuid.uuid4()


_Build = Callable[[AsyncSession, Actor], Awaitable[uuid.UUID]]


@pytest.mark.parametrize(
    ("build", "code", "status_code"),
    [
        (_draft, "market_not_approved", 409),
        (_submitted, "market_not_approved", 409),
        (_open, "market_not_approved", 409),
        (_closed, "market_not_approved", 409),
        (_pending, "market_pending_resolution", 409),
        (_unknown, "market_not_found", 404),
    ],
    ids=[
        "another_admins_draft",
        "another_admins_submission",
        "open",
        "closed",
        "pending_resolution",
        "unknown",
    ],
)
async def test_only_an_approved_market_can_be_sent_back(
    session: AsyncSession,
    audit_reader: AsyncSession,
    build: _Build,
    code: str,
    status_code: int,
) -> None:
    """ADR 0023's check order, step 3. A pending proposal keeps its own code
    because its remedy differs (review it, and reject). Another
    administrator's draft is found and refused, not 404, as close, approve
    and reject refuse one (ADR 0014). Nothing is written.
    """
    owner, sender = _actor(), _actor(username="carol_a")
    market_id = await build(session, owner)
    before = await session.scalar(select(Market.status).where(Market.id == market_id))

    with pytest.raises(MarketError) as refused:
        await market_service.send_back_outcome(
            session, sender, market_id, _send_back(None)
        )
    await session.rollback()

    assert refused.value.code == code
    assert refused.value.status_code == status_code
    assert await _committed_status(market_id) == before
    assert await _entries(audit_reader, sender, _SENT_BACK) == []


# --- which approval (CLAUDE.md, ADR 0016) ------------------------------------
async def _replaced_approval(
    session: AsyncSession, creator: Actor
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A market whose first approval was sent back by Bob, re-proposed by the
    creator and re-approved by Dave, all committed.

    Returns (market id, stale first proposal id, current id, second winner) as
    plain values, because the callers roll back and an expired entity is
    unreadable.
    """
    first = await approved_market(session, creator)
    market_id, stale, second_winner = first.id, first.proposal_id, first.outcomes[1].id

    await market_service.send_back_outcome(
        session, _actor(username="bob_b"), market_id, _send_back(stale)
    )
    second = await market_service.propose_outcome(
        session, creator, market_id, _proposal(second_winner)
    )
    current = second.proposal_id
    await market_service.approve_outcome(
        session, _actor(username="dave_d"), market_id, _approve(current)
    )
    return market_id, stale, current, second_winner


async def test_a_stale_send_back_does_not_send_back_the_replacement(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Carol is still reading the approval of P1 while the whole cycle (Bob
    sends it back, P2 is proposed, Dave approves P2) commits. Her send-back,
    quoting P1, must not send back P2, an approval she never saw. No row lock
    can catch this; the quoted id does. Fails without the id check.
    """
    carol = _actor(username="carol_a")
    market_id, stale, current, second_winner = await _replaced_approval(
        session, _actor()
    )

    with pytest.raises(MarketError) as refused:
        await market_service.send_back_outcome(
            session, carol, market_id, _send_back(stale)
        )
    await session.rollback()

    assert refused.value.code == "proposal_superseded"
    stored = await _stored(session, market_id)
    assert stored.status is MarketStatus.APPROVED
    assert stored.proposal_id == current
    assert stored.proposed_outcome_id == second_winner
    assert await _entries(audit_reader, carol, _SENT_BACK) == []


async def test_an_approval_from_before_ids_is_sent_back_by_quoting_null(
    session: AsyncSession,
) -> None:
    """ADR 0016's nullable promise, carried to send-back: an approval whose
    proposal predates ids is named by quoting null."""
    creator = _actor()
    pending = await proposed_before_ids(session, creator)
    market_id = pending.id
    await market_service.approve_outcome(
        session, _actor(username="ihsan_b"), market_id,
        OutcomeApprovalRequest(proposal_id=None),
    )

    sent = await market_service.send_back_outcome(
        session, _actor(), market_id,
        OutcomeSendBackRequest(proposal_id=None, reason=SEND_BACK_REASON),
    )

    assert sent.status is MarketStatus.CLOSED


# --- content and order -------------------------------------------------------
@pytest.mark.parametrize("reason", ["", "   ", "wrong"])
async def test_an_approved_result_is_not_sent_back_without_a_usable_reason(
    session: AsyncSession, audit_reader: AsyncSession, reason: str
) -> None:
    """The reason is the only record of why (D3), so a blank or one-word one
    is `422 send_back_incomplete` naming `reason`, and nothing is written."""
    sender = _actor()
    market = await approved_market(session, _actor())
    market_id = market.id

    with pytest.raises(MarketError) as refused:
        await market_service.send_back_outcome(
            session, sender, market_id, _send_back(market.proposal_id, reason=reason)
        )
    await session.rollback()

    assert refused.value.code == "send_back_incomplete"
    assert refused.value.status_code == 422
    assert [problem.field for problem in refused.value.problems] == ["reason"]
    assert await _committed_status(market_id) is MarketStatus.APPROVED
    assert await _entries(audit_reader, sender, _SENT_BACK) == []


async def test_the_window_is_checked_before_the_reason(session: AsyncSession) -> None:
    """ADR 0023's order: a closed window is final, whatever the reason says."""
    market = await approved_market(session, _actor())
    ends_at = market.dispute_window_ends_at

    with pytest.raises(MarketError) as refused:
        await market_service.send_back_outcome(
            session, _actor(), market.id,
            _send_back(market.proposal_id, reason="x"), now=ends_at,
        )

    assert refused.value.code == "dispute_window_closed"


async def test_a_stale_send_back_is_refused_before_its_reason_is_read(
    session: AsyncSession,
) -> None:
    """Which approval before the reason: nobody is asked to improve a reason
    about an approval that no longer exists (ADR 0016's order)."""
    market_id, stale, _, _ = await _replaced_approval(session, _actor())

    with pytest.raises(MarketError) as refused:
        await market_service.send_back_outcome(
            session, _actor(), market_id, _send_back(stale, reason="x")
        )

    assert refused.value.code == "proposal_superseded"


# --- the audit entry (ADR 0006, 0016, 0023) ----------------------------------
async def test_a_send_back_logs_what_was_approved_before_clearing_it(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """One `market.outcome_sent_back` under the sender, with the trimmed
    reason and the twelve keys as they were before the clear: after it, the
    entry is the only copy of what was approved, by whom, and when its window
    would have ended. Fails with the snapshot taken after the clear.
    """
    creator, approver = _actor(username="ernest_t"), _actor(username="ihsan_b")
    sender = _actor(username="carol_a")
    market = await approved_market(session, creator, approver=approver)
    market_id, proposal_id = market.id, market.proposal_id
    winner, proposed_at = market.proposed_outcome_id, market.proposed_at
    approved_at, ends_at = market.approved_at, market.dispute_window_ends_at
    sent_at = approved_at + timedelta(hours=1)

    await market_service.send_back_outcome(
        session, sender, market_id,
        _send_back(proposal_id, reason=f"  {SEND_BACK_REASON}  "),
        now=sent_at,
    )

    entries = await _entries(audit_reader, sender, _SENT_BACK)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["target_id"] == market_id
    assert entry["reason"] == SEND_BACK_REASON
    assert entry["occurred_at"] == sent_at
    assert entry["context"] == {
        "proposal_id": str(proposal_id),
        "winning_outcome_id": str(winner),
        "winning_outcome": "Yes",
        "evidence_url": EVIDENCE_URL,
        "evidence_note": EVIDENCE_NOTE,
        "proposed_by_id": str(creator.id),
        "proposed_by_username": "ernest_t",
        "proposed_at": proposed_at.isoformat(),
        "approved_by_id": str(approver.id),
        "approved_by_username": "ihsan_b",
        "approved_at": approved_at.isoformat(),
        "dispute_window_ends_at": ends_at.isoformat(),
    }


# --- two requests at once (ADR 0015) -----------------------------------------
async def _wait_until_blocked_on_a_lock(session: AsyncSession, pid: int) -> None:
    """Poll until backend `pid` waits on a lock, for at most two seconds.

    `pg_blocking_pids` reads the lock table live; `pg_stat_activity` would not,
    because it is snapshotted once per transaction. Auth's test_sessions.py
    has the twin.
    """
    stmt = text("SELECT cardinality(pg_blocking_pids(:pid)) > 0")
    for _ in range(200):
        if await session.scalar(stmt, {"pid": pid}):
            return
        await asyncio.sleep(0.01)
    # Carrying on would test the two sessions one after the other, not the race.
    pytest.fail(f"backend {pid} never blocked on a lock")


async def _backend_pid(session: AsyncSession) -> int:
    """Connect `session` and return its Postgres backend's pid."""
    return await session.scalar(text("SELECT pg_backend_pid()"))


async def _outcome(write: Awaitable[Market]) -> str:
    """The status a write left, or the code it was refused with."""
    try:
        return (await write).status.value
    except MarketError as refused:
        return refused.code


async def _approved_for_a_race() -> tuple[uuid.UUID, uuid.UUID, datetime]:
    """An approved market, committed: its id, `proposal_id` and window end."""
    async with get_session_factory()() as setup:
        market = await approved_market(setup, _actor())
        return market.id, market.proposal_id, market.dispute_window_ends_at


async def _queue_in_order(
    market_id: uuid.UUID,
    first: Callable[[AsyncSession], Awaitable[Market]],
    second: Callable[[AsyncSession], Awaitable[Market]],
) -> tuple[str, str]:
    """Run two writers on one row, `first` queued on the lock before `second`.

    Both sessions connect before anything starts. A third session holds the
    row; `first` starts and is seen waiting on the lock, then `second` starts
    and is seen waiting, then the row is released. The order is forced, not
    hoped for.
    """
    factory = get_session_factory()
    async with factory() as holder, factory() as early, factory() as late:
        early_pid = await _backend_pid(early)
        late_pid = await _backend_pid(late)
        tasks: list[asyncio.Task[str]] = []
        try:
            await holder.execute(
                select(Market.id).where(Market.id == market_id).with_for_update()
            )
            tasks.append(asyncio.create_task(_outcome(first(early))))
            await _wait_until_blocked_on_a_lock(holder, early_pid)
            tasks.append(asyncio.create_task(_outcome(second(late))))
            await _wait_until_blocked_on_a_lock(holder, late_pid)
        finally:
            # On every path, and before the writers' sessions close: one still
            # queued on the row would otherwise wait on this lock forever.
            await holder.rollback()
            answers = await asyncio.gather(*tasks, return_exceptions=True)

    for answer in answers:
        if isinstance(answer, BaseException):
            raise answer
    return answers[0], answers[1]


async def test_send_back_then_settle_on_one_row(
    clean_database, audit_reader: AsyncSession
) -> None:
    """Ihsan's race, send-back queued first. Each writer's `now` passes its
    own window check, so only the status read under the lock can refuse the
    loser: settle reads CLOSED and is `market_not_approved`.

    Fails with `for_update` removed from settle: it reads APPROVED unlocked,
    and its UPDATE then writes SETTLED over CLOSED.
    """
    sender, settler = _actor(), _actor(username="ihsan_b")
    market_id, proposal_id, ends_at = await _approved_for_a_race()

    sent, settled = await _queue_in_order(
        market_id,
        lambda own: market_service.send_back_outcome(
            own, sender, market_id, _send_back(proposal_id),
            now=ends_at - timedelta(minutes=1),
        ),
        lambda own: market_service.settle(
            own, settler, market_id, now=ends_at + _SETTLEMENT_GAP
        ),
    )

    assert (sent, settled) == ("closed", "market_not_approved")
    assert await _committed_status(market_id) is MarketStatus.CLOSED
    assert len(await _entries(audit_reader, sender, _SENT_BACK)) == 1
    assert await _entries(audit_reader, settler, "market.marked_settled") == []


async def test_settle_then_send_back_on_one_row(
    clean_database, audit_reader: AsyncSession
) -> None:
    """The mirror: settle queued first wins, and send-back reads SETTLED under
    the lock and is `market_already_settled`.

    Fails with `for_update` removed from send-back: it reads APPROVED
    unlocked, and its UPDATE then writes CLOSED over SETTLED.
    """
    sender, settler = _actor(), _actor(username="ihsan_b")
    market_id, proposal_id, ends_at = await _approved_for_a_race()

    settled, sent = await _queue_in_order(
        market_id,
        lambda own: market_service.settle(
            own, settler, market_id, now=ends_at + _SETTLEMENT_GAP
        ),
        lambda own: market_service.send_back_outcome(
            own, sender, market_id, _send_back(proposal_id),
            now=ends_at - timedelta(minutes=1),
        ),
    )

    assert (settled, sent) == ("settled", "market_already_settled")
    assert await _committed_status(market_id) is MarketStatus.SETTLED
    assert len(await _entries(audit_reader, settler, "market.marked_settled")) == 1
    assert await _entries(audit_reader, sender, _SENT_BACK) == []


async def test_two_concurrent_send_backs_send_back_once(
    clean_database, audit_reader: AsyncSession
) -> None:
    """A double click, or two administrators at once: one sends back, the
    other reads CLOSED under the lock and is `market_not_approved`, and the
    log holds one entry.

    A third session holds the row while both senders, already connected,
    read it. Fails with `for_update` removed from send-back: both read
    APPROVED, both commit, and the log holds two entries.
    """
    factory = get_session_factory()
    first, second = _actor(), _actor(username="carol_a")
    market_id, proposal_id, _ = await _approved_for_a_race()
    barrier = asyncio.Barrier(3)

    async def hold_the_row() -> None:
        async with factory() as own:
            await own.execute(
                select(Market.id).where(Market.id == market_id).with_for_update()
            )
            await barrier.wait()
            await asyncio.sleep(1)
            await own.rollback()

    async def send_back(sender: Actor) -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            return await _outcome(
                market_service.send_back_outcome(
                    own, sender, market_id, _send_back(proposal_id)
                )
            )

    _, first_answer, second_answer = await asyncio.gather(
        hold_the_row(), send_back(first), send_back(second)
    )

    assert sorted([first_answer, second_answer]) == ["closed", "market_not_approved"]
    assert await _committed_status(market_id) is MarketStatus.CLOSED
    entries = await _entries(audit_reader, first, _SENT_BACK) + await _entries(
        audit_reader, second, _SENT_BACK
    )
    assert len(entries) == 1
