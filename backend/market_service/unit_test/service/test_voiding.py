"""Marking a market voided, the ledger's last step, through the service layer.
[BE] #221, ADR 0025.

The ledger refunds every holder, commits, and then calls this with the
administrator's token and the recorded reason. The step locks the row, then:
VOIDED answers with nothing written, before the clock is read; SETTLED is
`market_already_settled`; CLOSED and PENDING_RESOLUTION are accepted; APPROVED
is accepted while Postgres's clock, read after the lock, is before the stored
window end plus five minutes; anything else is `market_not_voidable`. It
writes VOIDED, `voided_at` and the reason as sent, clears a pending proposal,
keeps an approval, and appends `market.marked_voided`. HTTP answers are in
unit_test/controller.

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

from core.database import get_session_factory
from core.errors import MarketError
from model.entities import Market, MarketStatus
from service import market_service
from service.audit import Actor
from unit_test.conftest import (
    EVIDENCE_NOTE,
    EVIDENCE_URL,
    VOID_REASON,
    approved_market,
    closed_market,
    draft_request,
    overdue_market,
    proposed_market,
    published_market,
    recorded_statements,
    settled_market,
    voided_market,
)
from unit_test.conftest import actor as _actor
from unit_test.conftest import approval_request as _approve
from unit_test.conftest import proposal_request as _proposal
from unit_test.conftest import rejection_request as _reject
from unit_test.conftest import send_back_request as _send_back
from unit_test.conftest import void_request as _void

# Read back as audit_svc: market_svc holds INSERT on this table and no SELECT.
_ENTRIES = text(
    "SELECT * FROM audit.admin_actions "
    "WHERE actor_id = :actor AND action_type = :action"
)

_MARKED_VOIDED = "market.marked_voided"

# The seven proposal columns `propose_outcome` writes together.
_PROPOSAL_COLUMNS = (
    "proposal_id",
    "proposed_outcome_id",
    "proposed_by_id",
    "proposed_by_username",
    "proposed_at",
    "proposal_evidence_url",
    "proposal_evidence_note",
)

# The approval's three columns and the stored window end.
_APPROVAL_COLUMNS = (
    "approved_by_id",
    "approved_by_username",
    "approved_at",
    "dispute_window_ends_at",
)

_ONE_MICROSECOND = timedelta(microseconds=1)

# "The `/void` step accepts five minutes more than the flag" (ADR 0025), named
# here rather than imported, as conftest.py does for settlement's gap.
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


def _columns(market: Market, names: tuple[str, ...]) -> dict[str, object]:
    return {name: getattr(market, name) for name in names}


async def _set_window_end(
    session: AsyncSession, market_id: uuid.UUID, ends_at: datetime | None
) -> None:
    await session.execute(
        update(Market).where(Market.id == market_id).values(dispute_window_ends_at=ends_at)
    )
    await session.commit()


async def _outcome(write: Awaitable[Market]) -> str:
    """The status a write left, or the code it was refused with."""
    try:
        return (await write).status.value
    except MarketError as refused:
        return refused.code


# --- the transition -------------------------------------------------------------
async def test_a_closed_market_is_voided_with_the_clock_read_after_the_lock(
    session: AsyncSession,
) -> None:
    """ADR 0025: "What a void writes. `status = VOIDED`, `voided_at` from the
    clock read after the lock, and `void_reason` as forwarded." Read from a
    second session, so this fails without the commit; the injected clock
    stands in for the one read after the lock."""
    market = await closed_market(session, _actor())
    market_id = market.id
    voided_at = market.closed_at + timedelta(minutes=10)

    voided = await market_service.void(
        session, _actor(username="ihsan_b"), market_id, _void(), now=voided_at
    )

    assert voided.status is MarketStatus.VOIDED
    assert await _committed_status(market_id) is MarketStatus.VOIDED
    stored = await _stored(session, market_id)
    assert stored.voided_at == voided_at
    assert stored.void_reason == VOID_REASON


async def test_the_reason_is_stored_as_sent_and_not_checked_again(
    session: AsyncSession,
) -> None:
    """ADR 0025: the step "does **not** re-check the reason. The ledger
    already did, and a stricter check here would fail the same way on every
    retry after the money moved." Three characters with spaces round them,
    which every reason rule here would refuse and trim; stored exactly."""
    market_id = (await closed_market(session, _actor())).id

    await market_service.void(session, _actor(), market_id, _void(reason="  Ab. "))

    assert (await _stored(session, market_id)).void_reason == "  Ab. "


async def test_a_pending_proposal_is_cleared_and_closed_at_kept(
    session: AsyncSession,
) -> None:
    """ADR 0025: "From PENDING_RESOLUTION it clears the seven proposal columns,
    as a rejection does. `closed_at` is left alone." """
    market = await proposed_market(session, _actor())
    market_id, closed_at = market.id, market.closed_at

    await market_service.void(session, _actor(), market_id, _void())

    stored = await _stored(session, market_id)
    assert _columns(stored, _PROPOSAL_COLUMNS) == dict.fromkeys(_PROPOSAL_COLUMNS)
    assert stored.closed_at == closed_at


async def test_an_approval_is_kept_as_history(session: AsyncSession) -> None:
    """ADR 0025: "From APPROVED it keeps the proposal and approval columns and
    `dispute_window_ends_at` as history, as settle does." Fails if the void
    clears them as it does a pending proposal."""
    market = await approved_market(session, _actor())
    market_id = market.id
    kept = _columns(market, _PROPOSAL_COLUMNS + _APPROVAL_COLUMNS)
    closed_at = market.closed_at

    await market_service.void(session, _actor(), market_id, _void())

    stored = await _stored(session, market_id)
    assert stored.status is MarketStatus.VOIDED
    assert _columns(stored, _PROPOSAL_COLUMNS + _APPROVAL_COLUMNS) == kept
    assert stored.closed_at == closed_at


# --- the window: five minutes more than the flag --------------------------------
async def test_an_approved_market_is_voided_until_five_minutes_after_its_window(
    session: AsyncSession,
) -> None:
    """ADR 0025: "accepts APPROVED while `clock_timestamp() <
    dispute_window_ends_at + SETTLEMENT_GAP`"; "With `E` and `E + 5 min`
    pinned, `/void` accepts until `E + 5 min` and refuses from it."

    Two markets with one stored end each, so neither attempt sees the
    other's write. Fails with `<=`, with the gap dropped (the early case is
    past `E`), or with the edge read from the setting rather than the row.
    """
    late = await approved_market(session, _actor())
    late_id, late_end = late.id, late.dispute_window_ends_at
    early = await approved_market(session, _actor())
    early_id, early_end = early.id, early.dispute_window_ends_at

    accepted = await _outcome(
        market_service.void(
            session, _actor(), early_id, _void(),
            now=early_end + _SETTLEMENT_GAP - _ONE_MICROSECOND,
        )
    )
    refused = await _outcome(
        market_service.void(
            session, _actor(), late_id, _void(), now=late_end + _SETTLEMENT_GAP
        )
    )

    assert accepted == "voided"
    assert refused == "market_not_voidable"


async def test_an_approved_market_with_no_window_end_cannot_be_voided(
    session: AsyncSession,
) -> None:
    """ADR 0025: "From that instant, or with no stored end,
    `409 market_not_voidable`." A null end is damage, and fails closed; the
    clock is the approval's own, so only the null can refuse."""
    market = await approved_market(session, _actor())
    market_id, approved_at = market.id, market.approved_at
    await _set_window_end(session, market_id, None)

    answer = await _outcome(
        market_service.void(session, _actor(), market_id, _void(), now=approved_at)
    )

    assert answer == "market_not_voidable"
    assert await _committed_status(market_id) is MarketStatus.APPROVED


# --- a repeat --------------------------------------------------------------------
async def test_a_repeat_answers_the_market_and_writes_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """ADR 0025: "VOIDED: `200`, nothing written, before the clock is read",
    and "A repeat appends nothing". A later clock and another reason, so a
    repeat that wrote would change both. Fails with VOIDED refused, or with
    the repeat falling through to the write."""
    first, again = _actor(), _actor(username="ihsan_b")
    market = await closed_market(session, _actor())
    market_id = market.id
    voided_at = market.closed_at + timedelta(minutes=10)
    await market_service.void(session, first, market_id, _void(), now=voided_at)

    repeated = await market_service.void(
        session, again, market_id, _void(reason="Some other reason, written later."),
        now=voided_at + timedelta(hours=1),
    )

    assert repeated.status is MarketStatus.VOIDED
    stored = await _stored(session, market_id)
    assert (stored.voided_at, stored.void_reason) == (voided_at, VOID_REASON)
    assert await _entries(audit_reader, again, _MARKED_VOIDED) == []


async def test_a_repeat_reads_no_clock(session: AsyncSession) -> None:
    """ADR 0025 and the contract: "`voided` answers `200` before the clock is
    read." Fails with the VOIDED check moved below the clock read."""
    market_id = (await voided_market(session, _actor())).id

    with recorded_statements() as statements:
        await market_service.void(session, _actor(), market_id, _void())

    assert not [s for s in statements if "clock_timestamp" in s.sql]


# --- refusals --------------------------------------------------------------------
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


async def _past_close_unswept(session: AsyncSession, owner: Actor) -> uuid.UUID:
    return (await overdue_market(session, owner)).id


async def _settled(session: AsyncSession, owner: Actor) -> uuid.UUID:
    return (await settled_market(session, owner)).id


async def _unknown(session: AsyncSession, owner: Actor) -> uuid.UUID:
    return uuid.uuid4()


_Build = Callable[[AsyncSession, Actor], Awaitable[uuid.UUID]]


@pytest.mark.parametrize(
    ("build", "code", "status_code"),
    [
        (_draft, "market_not_voidable", 409),
        (_submitted, "market_not_voidable", 409),
        (_open, "market_not_voidable", 409),
        (_past_close_unswept, "market_not_voidable", 409),
        (_settled, "market_already_settled", 409),
        (_unknown, "market_not_found", 404),
    ],
    ids=[
        "another_admins_draft",
        "another_admins_submission",
        "open",
        "past_close_unswept",
        "settled",
        "unknown",
    ],
)
async def test_only_a_market_that_stopped_and_is_not_settled_can_be_voided(
    session: AsyncSession, build: _Build, code: str, status_code: int
) -> None:
    """ADR 0025's order: SETTLED is `market_already_settled`; DRAFT, SUBMITTED
    and OPEN are `market_not_voidable`. Another administrator's draft is a
    409, not a 404, because any administrator may void (`get_any`).
    `past_close_unswept` is "The status column, not the clock": it has
    stopped trading and still reads OPEN. A refused void writes nothing."""
    market_id = await build(session, _actor())
    before = await _committed_status(market_id)

    with pytest.raises(MarketError) as refused:
        await market_service.void(session, _actor(username="ihsan_b"), market_id, _void())

    assert (refused.value.code, refused.value.status_code) == (code, status_code)
    assert await _committed_status(market_id) == before


# --- the audit entry ---------------------------------------------------------------
async def test_a_void_from_pending_logs_the_proposal_it_cleared(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """ADR 0025: "`market.marked_voided`, in the same transaction ... the
    market, the reason, the status it left, and any cleared proposal,
    snapshotted **before** the clear". Under the token's user, never a value
    from the body. Fails with the snapshot taken after the clear."""
    creator, voider = _actor(username="ernest_t"), _actor(username="ihsan_b")
    market = await proposed_market(session, creator)
    market_id, proposal_id = market.id, market.proposal_id
    winner, proposed_at = market.proposed_outcome_id, market.proposed_at
    voided_at = proposed_at + timedelta(minutes=10)

    await market_service.void(session, voider, market_id, _void(), now=voided_at)

    entries = await _entries(audit_reader, voider, _MARKED_VOIDED)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["target_id"] == market_id
    assert entry["reason"] == VOID_REASON
    assert entry["occurred_at"] == voided_at
    assert entry["context"] == {
        "from_status": "pending_resolution",
        "cleared_proposal": {
            "proposal_id": str(proposal_id),
            "winning_outcome_id": str(winner),
            "winning_outcome": "Yes",
            "evidence_url": EVIDENCE_URL,
            "evidence_note": EVIDENCE_NOTE,
            "proposed_by_id": str(creator.id),
            "proposed_by_username": "ernest_t",
            "proposed_at": proposed_at.isoformat(),
        },
    }


async def test_a_void_that_clears_nothing_logs_no_proposal(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """ADR 0025: "any cleared proposal". From APPROVED the proposal is kept on
    the market, so the entry clears none, and names the status it left.
    Fails if every void snapshots the proposal."""
    voider = _actor(username="ihsan_b")
    market_id = (await approved_market(session, _actor())).id

    await market_service.void(session, voider, market_id, _void())

    entries = await _entries(audit_reader, voider, _MARKED_VOIDED)
    assert [entry["context"] for entry in entries] == [
        {"from_status": "approved", "cleared_proposal": None}
    ]


# --- two requests on one row (ADR 0015) ---------------------------------------------
async def _wait_until_blocked_on_a_lock(session: AsyncSession, pid: int) -> None:
    """Poll until backend `pid` waits on a lock, for at most two seconds.

    `pg_blocking_pids` reads the lock table live; `pg_stat_activity` would not,
    because it is snapshotted once per transaction. test_sending_back.py has
    the twin.
    """
    stmt = text("SELECT cardinality(pg_blocking_pids(:pid)) > 0")
    for _ in range(200):
        if await session.scalar(stmt, {"pid": pid}):
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"backend {pid} never blocked on a lock")


async def _backend_pid(session: AsyncSession) -> int:
    """Connect `session` and return its Postgres backend's pid."""
    return await session.scalar(text("SELECT pg_backend_pid()"))


async def _queue_in_order(
    market_id: uuid.UUID,
    first: Callable[[AsyncSession], Awaitable[Market]],
    second: Callable[[AsyncSession], Awaitable[Market]],
) -> tuple[str, str]:
    """Run two writers on one row, `first` queued on the lock before `second`.

    Both writers connect and meet at an `asyncio.Barrier` before either
    starts, so neither runs alone. A third session holds the row; `first` is
    seen waiting on it before `second` starts, and `second` is seen waiting
    before the row is released. The order is forced, not hoped for.
    """
    factory = get_session_factory()
    barrier = asyncio.Barrier(3)

    async def writer(
        own: AsyncSession, write: Callable[[AsyncSession], Awaitable[Market]],
        go: asyncio.Event,
    ) -> str:
        await own.connection()
        await barrier.wait()
        await go.wait()
        return await _outcome(write(own))

    async with factory() as holder, factory() as early, factory() as late:
        early_pid = await _backend_pid(early)
        late_pid = await _backend_pid(late)
        early_go, late_go = asyncio.Event(), asyncio.Event()
        tasks = [
            asyncio.create_task(writer(early, first, early_go)),
            asyncio.create_task(writer(late, second, late_go)),
        ]
        try:
            await holder.execute(
                select(Market.id).where(Market.id == market_id).with_for_update()
            )
            await barrier.wait()
            early_go.set()
            await _wait_until_blocked_on_a_lock(holder, early_pid)
            late_go.set()
            await _wait_until_blocked_on_a_lock(holder, late_pid)
        finally:
            # On every path, and before the writers' sessions close: one still
            # queued on the row would otherwise wait on this lock forever.
            early_go.set()
            late_go.set()
            await holder.rollback()
            answers = await asyncio.gather(*tasks, return_exceptions=True)

    for answer in answers:
        if isinstance(answer, BaseException):
            raise answer
    return answers[0], answers[1]


async def _committed(build: Callable[[AsyncSession], Awaitable[Market]]) -> Market:
    """Build a market in a session of its own, committed, and hand it back."""
    async with get_session_factory()() as setup:
        return await build(setup)


async def test_two_voids_at_once_void_once(
    clean_database, audit_reader: AsyncSession
) -> None:
    """ADR 0025: "Void against void. One writes, both answer `200`". The
    second reads VOIDED under the lock and answers without writing, so the
    log holds one entry. Fails with `for_update` removed from the void: both
    read CLOSED and both commit an entry."""
    first, second = _actor(), _actor(username="ihsan_b")
    market_id = (await _committed(lambda s: closed_market(s, _actor()))).id

    answers = await _queue_in_order(
        market_id,
        lambda own: market_service.void(own, first, market_id, _void()),
        lambda own: market_service.void(own, second, market_id, _void()),
    )

    assert answers == ("voided", "voided")
    entries = await _entries(audit_reader, first, _MARKED_VOIDED) + await _entries(
        audit_reader, second, _MARKED_VOIDED
    )
    assert len(entries) == 1


async def test_settle_then_void_on_one_row(
    clean_database, audit_reader: AsyncSession
) -> None:
    """ADR 0025: "In market_service, the edges never meet", and when the
    ledger's two money paths are bypassed by direct calls, the row lock
    decides. Settle queued first wins; the void reads SETTLED under the lock
    and is `market_already_settled`. Each writer's `now` passes its own
    window check, so only the status read under the lock can refuse.

    Fails with `for_update` removed from the void: it reads APPROVED
    unlocked, and its UPDATE then writes VOIDED over SETTLED.
    """
    settler, voider = _actor(), _actor(username="ihsan_b")
    market = await _committed(lambda s: approved_market(s, _actor()))
    market_id, ends_at = market.id, market.dispute_window_ends_at

    settled, voided = await _queue_in_order(
        market_id,
        lambda own: market_service.settle(
            own, settler, market_id, now=ends_at + _SETTLEMENT_GAP
        ),
        lambda own: market_service.void(
            own, voider, market_id, _void(), now=ends_at - timedelta(minutes=1)
        ),
    )

    assert (settled, voided) == ("settled", "market_already_settled")
    assert await _committed_status(market_id) is MarketStatus.SETTLED
    assert await _entries(audit_reader, voider, _MARKED_VOIDED) == []


async def _closed_market(setup: AsyncSession) -> Market:
    return await closed_market(setup, _actor())


async def _pending_market(setup: AsyncSession) -> Market:
    return await proposed_market(setup, _actor())


async def _approved_market(setup: AsyncSession) -> Market:
    return await approved_market(setup, _actor())


def _propose_on(market: Market, admin: Actor) -> Callable[[AsyncSession], Awaitable[Market]]:
    # The proposer must be the creator; `get` scopes the read to them.
    return lambda own: market_service.propose_outcome(
        own, admin, market.id, _proposal(market.outcomes[0].id)
    )


def _approve_on(market: Market, admin: Actor) -> Callable[[AsyncSession], Awaitable[Market]]:
    return lambda own: market_service.approve_outcome(
        own, admin, market.id, _approve(market.proposal_id)
    )


def _reject_on(market: Market, admin: Actor) -> Callable[[AsyncSession], Awaitable[Market]]:
    return lambda own: market_service.reject_outcome(
        own, admin, market.id, _reject(market.proposal_id)
    )


def _send_back_on(market: Market, admin: Actor) -> Callable[[AsyncSession], Awaitable[Market]]:
    return lambda own: market_service.send_back_outcome(
        own, admin, market.id, _send_back(market.proposal_id)
    )


@pytest.mark.parametrize(
    ("build", "writer"),
    [
        (_closed_market, _propose_on),
        (_pending_market, _approve_on),
        (_pending_market, _reject_on),
        (_approved_market, _send_back_on),
    ],
    ids=["propose", "approve", "reject", "send_back"],
)
async def test_a_write_queued_behind_a_void_is_409_market_already_voided(
    clean_database,
    build: Callable[[AsyncSession], Awaitable[Market]],
    writer: Callable[[Market, Actor], Callable[[AsyncSession], Awaitable[Market]]],
) -> None:
    """ADR 0025: "Races on the market row, the void taking the row first:
    send-back, approve, reject and propose each answer `409
    market_already_voided`."

    Each fails with `for_update` removed from its own path: it reads the
    status before the void commits, and its UPDATE then writes over VOIDED.
    """
    market = await _committed(build)
    market_id = market.id
    # The creator proposes; anyone else decides or sends back.
    admin = Actor(id=market.creator_id, username="ernest_t", role="admin")
    if writer is not _propose_on:
        admin = _actor(username="ihsan_b")

    voided, written = await _queue_in_order(
        market_id,
        lambda own: market_service.void(own, _actor(), market_id, _void()),
        writer(market, admin),
    )

    assert (voided, written) == ("voided", "market_already_voided")
    assert await _committed_status(market_id) is MarketStatus.VOIDED
