"""Approving or rejecting a proposed outcome, driven through the service layer
without HTTP. [3.2] #10.

The two transitions out of PENDING_RESOLUTION and the rules guarding them.
Status codes and the error envelope live in unit_test/controller; what reaches
the audit log, which is where a rejection's reason and the proposal it cleared
go, lives in unit_test/service/test_audit.py.

Three things this suite is really about, under the obvious ones.

**The administrator who proposed can never decide.** Not by approving, and not
by rejecting either, because a proposer rejecting their own proposal is the
un-propose ADR 0013 declined to build. The rule compares ids, so a rename does
not get round it and a shared username does not trip it.

**The order of the refusals is part of the contract.** State before identity,
identity before content: a proposer looking at a market that is already
approved is told so rather than told they may not act, and a proposer sending a
one-word reason is told they may not act rather than told to write more.

**Each decision happens exactly once.** Every read that decides the write is
locked (ADR 0015), and the tests at the bottom are real races between real
transactions — each written to fail if that lock is removed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from core.errors import (
    MarketAlreadyApproved,
    MarketNotFound,
    MarketNotPendingResolution,
    ProposalSuperseded,
    RejectionIncomplete,
    SecondAdministratorRequired,
)
from model.entities import Market, MarketStatus
from model.schemas import OutcomeApprovalRequest, OutcomeRejectionRequest
from service import closing, market_service
from service.audit import Actor

# The suite's actor factory and market builders. Aliased rather than imported
# under their own names because every helper below takes an `actor` argument,
# which would shadow the first.
from unit_test.conftest import (
    REJECTION_REASON,
    closed_market,
    proposed_before_ids,
    proposed_market,
    published_market,
)
from unit_test.conftest import actor as _actor
from unit_test.conftest import approval_request as _approve
from unit_test.conftest import close_request as _close
from unit_test.conftest import draft_request as _request
from unit_test.conftest import proposal_request as _proposal
from unit_test.conftest import rejection_request as _reject

# Read back as audit_svc: market_svc holds INSERT on this table and no SELECT.
_ENTRIES = text(
    "SELECT id FROM audit.admin_actions "
    "WHERE actor_id = :actor AND action_type = :action"
)

# The seven columns one proposal occupies. An approval keeps every one of them —
# `proposed_outcome_id` is what [3.4] #12 settles against — and a rejection
# clears every one of them.
_PROPOSAL_COLUMNS = (
    "proposal_id",
    "proposed_outcome_id",
    "proposed_by_id",
    "proposed_by_username",
    "proposed_at",
    "proposal_evidence_url",
    "proposal_evidence_note",
)

_APPROVER_COLUMNS = ("approved_by_id", "approved_by_username", "approved_at")


def _columns(market: Market, names: tuple[str, ...]) -> dict[str, object]:
    return {name: getattr(market, name) for name in names}


async def _stored(session: AsyncSession) -> Market:
    """The one market in the database, as committed.

    Every suite here rebuilds the schema per test, so there is exactly one row
    unless a test made a second. Expired first, so the answer comes from
    Postgres rather than from the object the service just handed back.
    """
    session.expire_all()
    return (await session.execute(select(Market))).scalar_one()


async def _decide(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    decision: str,
    proposal_id: uuid.UUID | None,
) -> Market:
    """Approve, or reject with a usable reason, for the rules the two share.

    `proposal_id` is the market's own wherever it has one, so a refusal a test
    expects is refused for its own reason and not as `proposal_superseded`.
    """
    if decision == "approve":
        return await market_service.approve_outcome(
            session, actor, market_id, _approve(proposal_id)
        )
    return await market_service.reject_outcome(
        session, actor, market_id, _reject(proposal_id)
    )


async def _entry_count(audit_reader: AsyncSession, actor: Actor, action: str) -> int:
    rows = await audit_reader.execute(_ENTRIES, {"actor": actor.id, "action": action})
    return len(list(rows.mappings()))


async def _stopped_early_and_proposed(session: AsyncSession, proposer: Actor) -> Market:
    """A pending market whose closing time is still in the future.

    Reached through [2.3] #7's early close rather than the clock, for the tests
    that ask whether a market can be traded. Against a market the clock closed,
    `is_open_for_trading` answers False whatever this ticket writes to the
    status column, so those tests would pass while proving nothing.
    """
    market = await published_market(session, proposer)
    await market_service.close_early(session, proposer, market.id, _close())
    return await market_service.propose_outcome(
        session, proposer, market.id, _proposal(market.outcomes[0].id)
    )


# --- the transition, approving --------------------------------------------
async def test_a_pending_market_can_be_approved(session: AsyncSession) -> None:
    """The transition this ticket adds, out of PENDING_RESOLUTION."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    approved = await market_service.approve_outcome(session, approver, market.id, _approve(market.proposal_id))

    assert approved.status is MarketStatus.APPROVED
    assert approved.approved_at is not None
    assert approved.approved_by_id == approver.id


async def test_the_approval_survives_a_reload(session: AsyncSession) -> None:
    """Asserted from the database rather than the in-memory object, so this
    fails if the transition is never actually committed."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.approve_outcome(session, approver, market.id, _approve(market.proposal_id))

    stored = await _stored(session)
    assert stored.status is MarketStatus.APPROVED
    assert stored.approved_by_id == approver.id


async def test_both_identities_are_stored_on_the_market(session: AsyncSession) -> None:
    """[3.2] #10's third criterion: who proposed and who approved, on the row.

    Usernames beside the ids for the reason [3.1] #9 stored the proposer's:
    this service cannot resolve one from the other, so a name not written here
    is a name nobody can render later. ADR 0003.
    """
    proposer, approver = _actor(username="ernest_t"), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.approve_outcome(session, approver, market.id, _approve(market.proposal_id))

    stored = await _stored(session)
    assert stored.proposed_by_id == proposer.id
    assert stored.proposed_by_username == "ernest_t"
    assert stored.approved_by_id == approver.id
    assert stored.approved_by_username == "ihsan_b"
    assert stored.proposed_by_id != stored.approved_by_id


async def test_approving_records_when_it_happened(session: AsyncSession) -> None:
    """A timestamp of its own, and the one [3.3] #11's dispute window will be
    measured from. A proposal can sit for days before a second administrator
    gets to it, so reusing `proposed_at` would start that window early."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    later = datetime.now(UTC) + timedelta(days=2)

    approved = await market_service.approve_outcome(
        session, approver, market.id, _approve(market.proposal_id), now=later
    )

    assert approved.approved_at == later
    assert approved.approved_at != approved.proposed_at


async def test_an_approval_keeps_the_proposal(session: AsyncSession) -> None:
    """The winner is what was approved, so it stays exactly where it was.

    [3.4] #12 settles against `proposed_outcome_id`. An approval that tidied
    the proposal away, or rewrote the proposer as the approver, would leave
    settlement with nothing to pay out on.
    """
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    before = _columns(market, _PROPOSAL_COLUMNS)
    assert None not in before.values()

    await market_service.approve_outcome(session, approver, market.id, _approve(market.proposal_id))

    assert _columns(await _stored(session), _PROPOSAL_COLUMNS) == before


async def test_an_approval_leaves_the_terms_and_closed_at_alone(
    session: AsyncSession,
) -> None:
    """The request carries nothing, so nothing but the decision moves. The
    market a second administrator agreed to is the market traders traded, and
    `closed_at` still says when trading stopped."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer, question="The question traders saw?")
    close_time, closed_at = market.close_time, market.closed_at

    await market_service.approve_outcome(session, approver, market.id, _approve(market.proposal_id))

    stored = await _stored(session)
    assert stored.question == "The question traders saw?"
    assert [o.label for o in stored.outcomes] == ["Yes", "No"]
    assert stored.close_time == close_time
    assert stored.closed_at == closed_at


# --- the transition, rejecting --------------------------------------------
async def test_a_pending_market_can_be_rejected_back_to_closed(
    session: AsyncSession,
) -> None:
    """[3.2] #10's second criterion. Back to CLOSED, which is the state
    [3.1] #9 proposes from, so the market can be resolved again."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    rejected = await market_service.reject_outcome(
        session, rejecter, market.id, _reject(market.proposal_id)
    )

    assert rejected.status is MarketStatus.CLOSED


async def test_the_rejection_survives_a_reload(session: AsyncSession) -> None:
    """Asserted from the database rather than the in-memory object, so this
    fails if the transition is never actually committed."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.reject_outcome(session, rejecter, market.id, _reject(market.proposal_id))

    stored = await _stored(session)
    assert stored.status is MarketStatus.CLOSED
    assert stored.proposed_outcome_id is None


async def test_a_rejection_clears_every_proposal_column(session: AsyncSession) -> None:
    """All six, not only the winner.

    A CLOSED market with a leftover `proposed_by_id` would name a proposer for
    a proposal that no longer exists, and a leftover evidence URL would sit
    beside the next proposal as if it belonged to it. The rejected proposal
    survives in the audit log instead.
    """
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.reject_outcome(session, rejecter, market.id, _reject(market.proposal_id))

    stored = await _stored(session)
    assert _columns(stored, _PROPOSAL_COLUMNS) == dict.fromkeys(_PROPOSAL_COLUMNS)


async def test_a_rejection_leaves_closed_at_alone(session: AsyncSession) -> None:
    """`closed_at` says when trading stopped, and a rejection does not change
    that. Rewriting it would also blur the one signal that tells an early close
    from a clock close: a `closed_at` earlier than `close_time`."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    closed_at = market.closed_at
    assert closed_at is not None

    await market_service.reject_outcome(
        session, rejecter, market.id, _reject(market.proposal_id), now=datetime.now(UTC) + timedelta(days=2)
    )

    assert (await _stored(session)).closed_at == closed_at


async def test_a_rejection_leaves_the_approver_columns_null(
    session: AsyncSession,
) -> None:
    """The rejecter is named in the log and nowhere else. After a rejection the
    row holds no proposal, so there is nothing for a `rejected_by` to describe."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.reject_outcome(session, rejecter, market.id, _reject(market.proposal_id))

    stored = await _stored(session)
    assert _columns(stored, _APPROVER_COLUMNS) == dict.fromkeys(_APPROVER_COLUMNS)


async def test_a_rejection_leaves_the_terms_alone(session: AsyncSession) -> None:
    """The reason is about the proposal, not the market. The next proposal is
    made against the same terms traders traded."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer, question="The question traders saw?")
    close_time = market.close_time

    await market_service.reject_outcome(session, rejecter, market.id, _reject(market.proposal_id))

    stored = await _stored(session)
    assert stored.question == "The question traders saw?"
    assert [o.label for o in stored.outcomes] == ["Yes", "No"]
    assert stored.close_time == close_time


async def test_the_rejection_reason_is_not_stored_on_the_market(
    session: AsyncSession,
) -> None:
    """One fact, one place, as with an early close's reason. ADR 0014.

    A guard against somebody adding a `rejection_reason` column later and
    leaving two answers in the system — one of which a second rejection would
    overwrite, while the log keeps both.
    """
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    rejected = await market_service.reject_outcome(
        session, rejecter, market.id, _reject(market.proposal_id)
    )

    assert not any(
        REJECTION_REASON == getattr(rejected, column.key, None)
        for column in Market.__table__.columns
    )


async def test_a_rejected_market_can_be_proposed_for_again(
    session: AsyncSession,
) -> None:
    """The point of sending it back to CLOSED rather than somewhere new.

    A rejection is not the end of a market. Its creator proposes again — here
    the other outcome — down exactly the path the first proposal took.
    """
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    market_id, proposal_id, second = market.id, market.proposal_id, market.outcomes[1].id

    await market_service.reject_outcome(session, rejecter, market_id, _reject(proposal_id))
    proposed = await market_service.propose_outcome(
        session, proposer, market_id, _proposal(second)
    )

    assert proposed.status is MarketStatus.PENDING_RESOLUTION
    assert proposed.proposed_outcome_id == second


async def test_a_rejected_market_takes_no_more_trades(session: AsyncSession) -> None:
    """Back to CLOSED, not back to OPEN.

    Reached through an early close, so `close_time` is still in the future and
    only the status can be what answers False. A rejection that reopened
    trading would let people bet on a question an administrator has just
    publicly tried to answer.
    """
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await _stopped_early_and_proposed(session, proposer)

    rejected = await market_service.reject_outcome(
        session, rejecter, market.id, _reject(market.proposal_id)
    )

    assert closing.is_open_for_trading(rejected) is False


# --- who may decide -------------------------------------------------------
async def test_the_proposer_cannot_approve(session: AsyncSession) -> None:
    """[3.2] #10's first criterion, in the half this service owns.

    A disabled button is a hint; this is the rule behind it. Without it one
    administrator names a winner and agrees with themselves, and the second
    signature is decoration.
    """
    proposer = _actor()
    market = await proposed_market(session, proposer)

    with pytest.raises(SecondAdministratorRequired):
        await market_service.approve_outcome(session, proposer, market.id, _approve(market.proposal_id))


async def test_the_proposer_cannot_reject(session: AsyncSession) -> None:
    """Not only approval. A proposer rejecting their own proposal is a
    withdrawal, which ADR 0013 declined: a second path back to CLOSED with no
    second administrator in it."""
    proposer = _actor()
    market = await proposed_market(session, proposer)

    with pytest.raises(SecondAdministratorRequired):
        await market_service.reject_outcome(session, proposer, market.id, _reject(market.proposal_id))


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_a_refused_proposer_writes_nothing(
    session: AsyncSession, decision: str
) -> None:
    """The refusal must actually protect the row, not merely report."""
    proposer = _actor()
    market = await proposed_market(session, proposer)
    market_id, proposal_id, winner = (
        market.id, market.proposal_id, market.proposed_outcome_id
    )

    with pytest.raises(SecondAdministratorRequired):
        await _decide(session, proposer, market_id, decision, proposal_id)

    await session.rollback()
    stored = await _stored(session)
    assert stored.status is MarketStatus.PENDING_RESOLUTION
    assert stored.approved_by_id is None
    assert stored.proposed_outcome_id == winner


async def test_the_rule_compares_the_id_not_the_username(session: AsyncSession) -> None:
    """Usernames are a snapshot for display. Two administrators may share one
    over time, and a rule keyed on it would refuse a legitimate second pair of
    eyes."""
    proposer = _actor(username="ernest_t")
    market = await proposed_market(session, proposer)
    namesake = Actor(id=uuid.uuid4(), username=proposer.username, role="admin")

    approved = await market_service.approve_outcome(session, namesake, market.id, _approve(market.proposal_id))

    assert approved.status is MarketStatus.APPROVED


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_the_proposer_under_a_new_username_is_still_refused(
    session: AsyncSession, decision: str
) -> None:
    """The other direction of the same rule. A rename between proposing and
    deciding must not turn the proposer into somebody else."""
    proposer = _actor(username="ernest_t")
    market = await proposed_market(session, proposer)
    renamed = Actor(id=proposer.id, username="renamed", role="admin")

    with pytest.raises(SecondAdministratorRequired):
        await _decide(session, renamed, market.id, decision, market.proposal_id)


@pytest.mark.parametrize(
    ("decision", "status"),
    [("approve", MarketStatus.APPROVED), ("reject", MarketStatus.CLOSED)],
)
async def test_any_other_administrator_may_decide_a_market_they_did_not_create(
    session: AsyncSession, decision: str, status: MarketStatus
) -> None:
    """The second read in this service not scoped to the creator. ADR 0013
    named this ticket as the one that widens it.

    It has to be: only the creator may propose, and the proposer may not
    decide, so a decision scoped to the creator could never be made by anyone.
    """
    owner, overseer = _actor(username="ernest_t"), _actor(username="ihsan_b")
    market = await proposed_market(session, owner)

    decided = await _decide(session, overseer, market.id, decision, market.proposal_id)

    assert decided.status is status
    assert decided.creator_id == owner.id


async def test_the_creator_scoped_read_is_untouched(session: AsyncSession) -> None:
    """Widening the decision must not have widened anything else.

    `get_any` is one call away from `get`. If this ever passes for the second
    administrator, the unscoped read has escaped into the one every other route
    uses, and drafts have started leaking.
    """
    owner, other = _actor(), _actor()
    market = await proposed_market(session, owner)

    with pytest.raises(MarketNotFound):
        await market_service.get(session, other.id, market.id)


# --- which proposal is being decided ---------------------------------------
async def _replaced(
    session: AsyncSession, proposer: Actor, rejecter: Actor
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A market whose first proposal was rejected and replaced by a second.

    Returns the market id, the first proposal's id — what a reviewer who opened
    the market before the rejection is still holding — the second proposal's
    id, and the outcome the second one names. Plain values rather than the
    entity, because the tests that use this roll back after a refusal and an
    async ORM instance is unreadable once they do.
    """
    first = await proposed_market(session, proposer)
    market_id, stale, second_winner = first.id, first.proposal_id, first.outcomes[1].id

    await market_service.reject_outcome(session, rejecter, market_id, _reject(stale))
    second = await market_service.propose_outcome(
        session, proposer, market_id, _proposal(second_winner)
    )
    return market_id, stale, second.proposal_id, second_winner


async def test_proposing_gives_the_proposal_an_id(session: AsyncSession) -> None:
    market = await proposed_market(session, _actor())

    assert isinstance(market.proposal_id, uuid.UUID)


async def test_a_replacement_proposal_gets_a_new_id(session: AsyncSession) -> None:
    """Even for the same creator on the same market. The id is what tells two
    proposals apart when the status, the market and the proposer are all the
    same — which is exactly the case a stale decision arrives in."""
    _, stale, current, _ = await _replaced(
        session, _actor(), _actor(username="ihsan_b")
    )

    assert current is not None
    assert current != stale


async def test_a_stale_approval_does_not_approve_the_replacement(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The Codex P1 on this ticket, as a test.

    A reviewer opens proposal 1. Somebody else rejects it and the creator
    proposes 2, naming the other outcome. The reviewer, still looking at 1,
    presses approve. Keyed only by the market this approved 2 — a winner the
    reviewer never saw — and no row lock could stop it, because nothing was
    overlapping: the reject-and-repropose had committed before the approval
    arrived. The quoted proposal id is what catches it.
    """
    proposer, reviewer = _actor(), _actor(username="ihsan_b")
    market_id, stale, current, second_winner = await _replaced(
        session, proposer, _actor()
    )

    with pytest.raises(ProposalSuperseded):
        await market_service.approve_outcome(
            session, reviewer, market_id, _approve(stale)
        )

    await session.rollback()
    stored = await _stored(session)
    assert stored.status is MarketStatus.PENDING_RESOLUTION
    assert stored.proposal_id == current
    assert stored.proposed_outcome_id == second_winner
    assert stored.approved_by_id is None
    assert await _entry_count(audit_reader, reviewer, "market.outcome_approved") == 0


async def test_a_stale_rejection_does_not_clear_the_replacement(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The same stale page, the other button. A reason written about proposal 1
    must not clear proposal 2 and sit in the log as the reason 2 was refused."""
    proposer, reviewer = _actor(), _actor(username="ihsan_b")
    market_id, stale, current, second_winner = await _replaced(
        session, proposer, _actor()
    )

    with pytest.raises(ProposalSuperseded):
        await market_service.reject_outcome(
            session, reviewer, market_id, _reject(stale)
        )

    await session.rollback()
    stored = await _stored(session)
    assert stored.status is MarketStatus.PENDING_RESOLUTION
    assert stored.proposal_id == current
    assert stored.proposed_outcome_id == second_winner
    assert await _entry_count(audit_reader, reviewer, "market.outcome_rejected") == 0


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_the_current_proposal_can_still_be_decided(
    session: AsyncSession, decision: str
) -> None:
    """The refusal is about the quoted id, not about the market having been
    proposed for twice: a reviewer who reloads and quotes proposal 2 decides it."""
    market_id, _, current, _ = await _replaced(
        session, _actor(), _actor(username="ihsan_b")
    )

    decided = await _decide(session, _actor(), market_id, decision, current)

    assert decided.status in (MarketStatus.APPROVED, MarketStatus.CLOSED)


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_an_id_no_proposal_ever_had_is_refused(
    session: AsyncSession, decision: str
) -> None:
    market = await proposed_market(session, _actor())

    with pytest.raises(ProposalSuperseded):
        await _decide(
            session, _actor(username="ihsan_b"), market.id, decision, uuid.uuid4()
        )


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_a_stale_proposer_is_told_they_may_not_decide_at_all(
    session: AsyncSession, decision: str
) -> None:
    """Identity before which proposal. Reloading would not help the proposer,
    so `proposal_superseded` — whose remedy is to reload — would mislead."""
    proposer = _actor()
    market_id, stale, _, _ = await _replaced(
        session, proposer, _actor(username="ihsan_b")
    )

    with pytest.raises(SecondAdministratorRequired):
        await _decide(session, proposer, market_id, decision, stale)


async def test_a_stale_rejection_is_refused_before_its_reason_is_read(
    session: AsyncSession,
) -> None:
    """Which proposal before the reason: nobody should be asked to lengthen a
    reason about a proposal that is no longer there."""
    market_id, stale, _, _ = await _replaced(
        session, _actor(), _actor(username="ihsan_b")
    )

    with pytest.raises(ProposalSuperseded):
        await market_service.reject_outcome(
            session, _actor(), market_id, _reject(stale, reason="x")
        )


# A proposal made before proposal ids existed is pending with a null one, and is
# decided by quoting null. `sql/migrations/0006` explains why it is not
# backfilled; these are the tests that a null opens nothing else.
def _quoting_null(decision: str) -> OutcomeApprovalRequest | OutcomeRejectionRequest:
    if decision == "approve":
        return OutcomeApprovalRequest(proposal_id=None)
    return OutcomeRejectionRequest(proposal_id=None, reason=REJECTION_REASON)


async def _decide_quoting_null(
    session: AsyncSession, actor: Actor, market_id: uuid.UUID, decision: str
) -> Market:
    if decision == "approve":
        return await market_service.approve_outcome(
            session, actor, market_id, _quoting_null(decision)
        )
    return await market_service.reject_outcome(
        session, actor, market_id, _quoting_null(decision)
    )


@pytest.mark.parametrize(
    ("decision", "status"),
    [("approve", MarketStatus.APPROVED), ("reject", MarketStatus.CLOSED)],
)
async def test_a_proposal_from_before_ids_is_decided_by_quoting_null(
    session: AsyncSession, decision: str, status: MarketStatus
) -> None:
    """The migration's promise. Without this, a proposal already waiting when
    0006 ran could never be decided by anybody allowed to decide it."""
    market = await proposed_before_ids(session, _actor())
    assert market.proposal_id is None

    decided = await _decide_quoting_null(
        session, _actor(username="ihsan_b"), market.id, decision
    )

    assert decided.status is status


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_quoting_null_does_not_decide_a_proposal_that_has_an_id(
    session: AsyncSession, decision: str
) -> None:
    """A null is not a wildcard. It matches the absence of an id, and every
    proposal made since ids existed has one."""
    market = await proposed_market(session, _actor())

    with pytest.raises(ProposalSuperseded):
        await _decide_quoting_null(
            session, _actor(username="ihsan_b"), market.id, decision
        )


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_a_stale_null_does_not_decide_the_replacement_of_an_old_proposal(
    session: AsyncSession, decision: str
) -> None:
    """The Codex P1 scenario, starting from a proposal that predates ids. A
    reviewer read the old proposal and holds a null; it is rejected and
    replaced; the replacement has an id, so the stale null is refused."""
    proposer = _actor()
    old = await proposed_before_ids(session, proposer)
    market_id, second_winner = old.id, old.outcomes[1].id

    await market_service.reject_outcome(
        session, _actor(), market_id, _quoting_null("reject")
    )
    await market_service.propose_outcome(
        session, proposer, market_id, _proposal(second_winner)
    )

    with pytest.raises(ProposalSuperseded):
        await _decide_quoting_null(
            session, _actor(username="ihsan_b"), market_id, decision
        )


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_an_old_proposal_quoted_by_a_made_up_id_is_refused(
    session: AsyncSession, decision: str
) -> None:
    market = await proposed_before_ids(session, _actor())

    with pytest.raises(ProposalSuperseded):
        await _decide(
            session, _actor(username="ihsan_b"), market.id, decision, uuid.uuid4()
        )


async def test_an_approved_market_answers_already_approved_to_a_stale_id(
    session: AsyncSession,
) -> None:
    """State before which proposal. The market is past deciding, and that is
    the fact worth reporting, whichever proposal the caller last saw."""
    market = await proposed_market(session, _actor())
    await market_service.approve_outcome(
        session, _actor(username="ihsan_b"), market.id, _approve(market.proposal_id)
    )

    with pytest.raises(MarketAlreadyApproved):
        await market_service.approve_outcome(
            session, _actor(), market.id, _approve(uuid.uuid4())
        )


# --- which markets may be decided -----------------------------------------
@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("status", ["draft", "submitted"])
async def test_a_market_that_never_opened_cannot_be_decided(
    session: AsyncSession, status: str, decision: str
) -> None:
    """There is no proposal on it. A 409 rather than a 404 even to another
    administrator — the same trade ADR 0014 made for an early close, because
    a decision refuses anything without a proposal before it acts."""
    owner = _actor()
    market, _, _ = await market_service.save(session, owner, _request(status=status))

    with pytest.raises(MarketNotPendingResolution):
        await _decide(session, _actor(username="ihsan_b"), market.id, decision, market.proposal_id)


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_an_open_market_cannot_be_decided(
    session: AsyncSession, decision: str
) -> None:
    owner = _actor()
    market = await published_market(session, owner)

    with pytest.raises(MarketNotPendingResolution):
        await _decide(session, _actor(username="ihsan_b"), market.id, decision, market.proposal_id)


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_a_closed_market_with_no_proposal_cannot_be_decided(
    session: AsyncSession, decision: str
) -> None:
    """The closest miss. CLOSED is the state just before a proposal, and there
    is nothing yet for a second administrator to agree or disagree with."""
    owner = _actor()
    market = await closed_market(session, owner)

    with pytest.raises(MarketNotPendingResolution):
        await _decide(session, _actor(username="ihsan_b"), market.id, decision, market.proposal_id)


async def test_approving_twice_is_refused(session: AsyncSession) -> None:
    """By anybody. The second approver here is a third administrator, so the
    identity rule would let them through; the state is what refuses them."""
    proposer = _actor()
    market = await proposed_market(session, proposer)
    await market_service.approve_outcome(session, _actor(username="ihsan_b"), market.id, _approve(market.proposal_id))

    with pytest.raises(MarketAlreadyApproved):
        await market_service.approve_outcome(session, _actor(), market.id, _approve(market.proposal_id))


async def test_a_second_approval_does_not_overwrite_the_first(
    session: AsyncSession,
) -> None:
    """The refusal above must actually protect the row, not merely report.

    A later clock and a different administrator, so an overwrite would show in
    either column.
    """
    proposer, first, second = _actor(), _actor(username="ihsan_b"), _actor()
    market = await proposed_market(session, proposer)
    market_id, proposal_id = market.id, market.proposal_id
    approved = await market_service.approve_outcome(session, first, market_id, _approve(proposal_id))
    approved_at = approved.approved_at

    with pytest.raises(MarketAlreadyApproved):
        await market_service.approve_outcome(
            session, second, market_id, _approve(proposal_id), now=datetime.now(UTC) + timedelta(days=2)
        )

    await session.rollback()
    stored = await _stored(session)
    assert stored.approved_by_id == first.id
    assert stored.approved_at == approved_at


async def test_rejecting_an_approved_market_is_refused(session: AsyncSession) -> None:
    """An approval is final as far as this ticket goes. Undoing one is
    [3.3] #11's dispute, with its own window and its own record — not a
    rejection that happens to arrive late."""
    proposer = _actor()
    market = await proposed_market(session, proposer)
    market_id, proposal_id = market.id, market.proposal_id
    await market_service.approve_outcome(session, _actor(username="ihsan_b"), market_id, _approve(proposal_id))

    with pytest.raises(MarketAlreadyApproved):
        await market_service.reject_outcome(session, _actor(), market_id, _reject(proposal_id))

    await session.rollback()
    stored = await _stored(session)
    assert stored.status is MarketStatus.APPROVED
    assert None not in _columns(stored, _PROPOSAL_COLUMNS).values()


async def test_rejecting_twice_is_refused(session: AsyncSession) -> None:
    """The first rejection sent it back to CLOSED with no proposal on it, so
    the second finds nothing to reject — the same answer as a market that was
    never proposed for, because that is now exactly what it is."""
    proposer = _actor()
    market = await proposed_market(session, proposer)
    await market_service.reject_outcome(
        session, _actor(username="ihsan_b"), market.id, _reject(market.proposal_id)
    )

    with pytest.raises(MarketNotPendingResolution):
        await market_service.reject_outcome(session, _actor(), market.id, _reject(market.proposal_id))


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_an_unknown_market_is_not_found(
    session: AsyncSession, decision: str
) -> None:
    with pytest.raises(MarketNotFound):
        await _decide(session, _actor(), uuid.uuid4(), decision, None)


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_an_approved_market_tells_the_proposer_it_is_approved(
    session: AsyncSession, decision: str
) -> None:
    """State before identity. The proposer's remedy here is not to find another
    administrator — one already has — so the refusal says that instead."""
    proposer = _actor()
    market = await proposed_market(session, proposer)
    await market_service.approve_outcome(session, _actor(username="ihsan_b"), market.id, _approve(market.proposal_id))

    with pytest.raises(MarketAlreadyApproved):
        await _decide(session, proposer, market.id, decision, market.proposal_id)


async def test_the_identity_is_checked_before_the_reason(session: AsyncSession) -> None:
    """A proposer is not told to improve the wording of a rejection they are
    not allowed to make."""
    proposer = _actor()
    market = await proposed_market(session, proposer)

    with pytest.raises(SecondAdministratorRequired):
        await market_service.reject_outcome(
            session, proposer, market.id, _reject(market.proposal_id, reason="x")
        )


# --- the reason -----------------------------------------------------------
@pytest.mark.parametrize("reason", ["", "   ", "wrong"])
async def test_a_proposal_is_not_rejected_without_a_usable_reason(
    session: AsyncSession, reason: str
) -> None:
    """The rules themselves are in unit_test/service/test_validation.py; this
    is that they are enforced here."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    with pytest.raises(RejectionIncomplete) as raised:
        await market_service.reject_outcome(
            session, rejecter, market.id, _reject(market.proposal_id, reason=reason)
        )

    assert [p.field for p in raised.value.problems] == ["reason"]


async def test_a_refused_rejection_leaves_the_market_pending(
    session: AsyncSession,
) -> None:
    """All-or-nothing. Clearing the proposal before refusing would lose the
    thing the rejecter was about to explain, with no log entry to recover it
    from."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    market_id, proposal_id, winner = (
        market.id, market.proposal_id, market.proposed_outcome_id
    )

    with pytest.raises(RejectionIncomplete):
        await market_service.reject_outcome(
            session, rejecter, market_id, _reject(proposal_id, reason="x")
        )

    await session.rollback()
    stored = await _stored(session)
    assert stored.status is MarketStatus.PENDING_RESOLUTION
    assert stored.proposed_outcome_id == winner


async def test_the_state_is_checked_before_the_reason(session: AsyncSession) -> None:
    """A market with nothing to reject is not told to fix its wording. The
    same ordering `close_early` uses, and the same argument."""
    owner = _actor()
    market = await closed_market(session, owner)

    with pytest.raises(MarketNotPendingResolution):
        await market_service.reject_outcome(
            session, _actor(username="ihsan_b"), market.id, _reject(market.proposal_id, reason="x")
        )


# --- an approved market is frozen -----------------------------------------
async def _approved(session: AsyncSession, proposer: Actor, **overrides: object) -> Market:
    market = await proposed_market(session, proposer, **overrides)
    return await market_service.approve_outcome(
        session, _actor(username="ihsan_b"), market.id, _approve(market.proposal_id)
    )


async def test_a_late_autosave_cannot_touch_an_approved_market(
    session: AsyncSession,
) -> None:
    """The form may still be open behind everything that has happened since.

    A save here would change the terms two administrators have just agreed an
    outcome for, or — without the frozen check — write DRAFT over APPROVED on
    the next three-second tick.
    """
    proposer = _actor()
    key = uuid.uuid4()
    await _approved(session, proposer, draft_key=key, question="The approved question?")

    with pytest.raises(MarketAlreadyApproved):
        await market_service.save(
            session, proposer, _request(draft_key=key, question="Edited?")
        )

    await session.rollback()
    stored = await _stored(session)
    assert stored.status is MarketStatus.APPROVED
    assert stored.question == "The approved question?"


async def test_an_approved_market_cannot_be_published(session: AsyncSession) -> None:
    """The refusal names the state it is actually in, rather than reporting
    that it is not submitted."""
    proposer = _actor()
    market = await _approved(session, proposer)

    with pytest.raises(MarketAlreadyApproved):
        await market_service.publish(session, proposer, market.id)


async def test_an_approved_market_cannot_be_proposed_for_again(
    session: AsyncSession,
) -> None:
    """Not `market_not_closed`, which would send the creator off to wait for a
    market that has already been decided."""
    proposer = _actor()
    market = await _approved(session, proposer)

    with pytest.raises(MarketAlreadyApproved):
        await market_service.propose_outcome(
            session, proposer, market.id, _proposal(market.outcomes[1].id)
        )


async def test_an_approved_market_cannot_be_closed_early(session: AsyncSession) -> None:
    """Not `market_closed`, which is true and useless: the administrator should
    be shown that an outcome has been agreed, not that trading has stopped."""
    proposer = _actor()
    market = await _approved(session, proposer)

    with pytest.raises(MarketAlreadyApproved):
        await market_service.close_early(session, _actor(), market.id, _close())


async def test_an_approved_market_is_not_tradeable(session: AsyncSession) -> None:
    """Asked of both halves of the rule the trade path and the browse query use.

    Reached through an early close, so `close_time` is still in the future and
    the status is the only thing that can answer.
    """
    proposer = _actor()
    market = await _stopped_early_and_proposed(session, proposer)
    approved = await market_service.approve_outcome(
        session, _actor(username="ihsan_b"), market.id, _approve(market.proposal_id)
    )

    assert closing.is_open_for_trading(approved) is False

    tradeable = (
        await session.execute(select(Market.id).where(closing.open_for_trading()))
    ).scalars().all()
    assert approved.id not in tradeable


async def test_the_sweeper_leaves_an_approved_market_alone(
    session: AsyncSession,
) -> None:
    """It is not `open`, so it is not in the sweep's working set at all.

    What matters is that the sweep never writes CLOSED over APPROVED when a
    market stopped early reaches the closing time it was stopped short of.
    """
    proposer = _actor()
    market = await _stopped_early_and_proposed(session, proposer)
    approved = await market_service.approve_outcome(
        session, _actor(username="ihsan_b"), market.id, _approve(market.proposal_id)
    )
    closed_at = approved.closed_at

    approved.close_time = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    assert await closing.close_due_markets(session, limit=10) == []

    stored = await _stored(session)
    assert stored.status is MarketStatus.APPROVED
    assert stored.closed_at == closed_at


# --- two requests at once -------------------------------------------------
async def test_two_concurrent_approvals_produce_one_winner(
    clean_database, audit_reader: AsyncSession
) -> None:
    """An administrator double-clicks the approve button.

    Two real transactions, not a simulated race. Read without a lock both would
    see PENDING_RESOLUTION, both would write APPROVED, and the log would carry
    two entries for one approval, each with its own `approved_at`.

    `approve_outcome` takes a row lock, so under READ COMMITTED the loser
    blocks on the SELECT and then re-reads the row the winner committed. It
    finds APPROVED and is refused.
    """
    factory = get_session_factory()
    proposer, approver = _actor(), _actor(username="ihsan_b")

    async with factory() as setup:
        pending = await proposed_market(setup, proposer)
        market_id, proposal_id = pending.id, pending.proposal_id

    # Both connected before either starts, so the outcome is decided by the
    # lock and not by which session had to open a connection first.
    barrier = asyncio.Barrier(2)

    async def attempt() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await market_service.approve_outcome(own, approver, market_id, _approve(proposal_id))
            except MarketAlreadyApproved:
                return "refused"
            return "approved"

    results = await asyncio.gather(attempt(), attempt())

    assert sorted(results) == ["approved", "refused"]

    async with factory() as check:
        stored = (
            await check.execute(select(Market).where(Market.id == market_id))
        ).scalar_one()
    assert stored.status is MarketStatus.APPROVED
    assert stored.approved_by_id == approver.id

    approvals = await _entry_count(audit_reader, approver, "market.outcome_approved")
    assert approvals == 1, "the log must record one approval, not two"


async def test_two_concurrent_rejections_produce_one_winner(
    clean_database, audit_reader: AsyncSession
) -> None:
    """Two administrators reject the same proposal in the same instant.

    Read without a lock both would see PENDING_RESOLUTION, both would clear the
    proposal, and the log would carry two rejections with two different
    reasons for one proposal — the second of them rejecting something that no
    longer existed. The loser re-reads CLOSED under the lock and is told there
    is nothing to reject.
    """
    factory = get_session_factory()
    proposer = _actor()
    first, second = _actor(username="ihsan_b"), _actor()

    async with factory() as setup:
        pending = await proposed_market(setup, proposer)
        market_id, proposal_id = pending.id, pending.proposal_id

    barrier = asyncio.Barrier(2)

    async def attempt(rejecter: Actor) -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await market_service.reject_outcome(own, rejecter, market_id, _reject(proposal_id))
            except MarketNotPendingResolution:
                return "refused"
            return "rejected"

    results = await asyncio.gather(attempt(first), attempt(second))

    assert sorted(results) == ["refused", "rejected"]

    async with factory() as check:
        stored = (
            await check.execute(select(Market).where(Market.id == market_id))
        ).scalar_one()
    assert stored.status is MarketStatus.CLOSED
    assert _columns(stored, _PROPOSAL_COLUMNS) == dict.fromkeys(_PROPOSAL_COLUMNS)

    rejections = await _entry_count(
        audit_reader, first, "market.outcome_rejected"
    ) + await _entry_count(audit_reader, second, "market.outcome_rejected")
    assert rejections == 1, "the log must record one rejection, not two"


async def test_an_approval_racing_a_rejection_produces_exactly_one_decision(
    clean_database, audit_reader: AsyncSession
) -> None:
    """Two administrators open the same proposal and disagree.

    Either order is legitimate, so the assertions follow whoever won rather
    than naming a winner. What can never happen is both: a market marked
    APPROVED whose proposal a rejection has cleared, or two entries in the log
    recording opposite decisions about one proposal.
    """
    factory = get_session_factory()
    proposer = _actor()
    approver, rejecter = _actor(username="ihsan_b"), _actor()

    async with factory() as setup:
        pending = await proposed_market(setup, proposer)
        market_id, proposal_id = pending.id, pending.proposal_id

    barrier = asyncio.Barrier(2)

    async def approve() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await market_service.approve_outcome(own, approver, market_id, _approve(proposal_id))
            except MarketNotPendingResolution:
                return "refused"
            return "approved"

    async def reject() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await market_service.reject_outcome(own, rejecter, market_id, _reject(proposal_id))
            except MarketAlreadyApproved:
                return "refused"
            return "rejected"

    results = await asyncio.gather(approve(), reject())

    assert results.count("refused") == 1, f"exactly one decision, got {results}"

    async with factory() as check:
        stored = (
            await check.execute(select(Market).where(Market.id == market_id))
        ).scalar_one()

    approvals = await _entry_count(audit_reader, approver, "market.outcome_approved")
    rejections = await _entry_count(audit_reader, rejecter, "market.outcome_rejected")
    assert approvals + rejections == 1, "the log must record one decision, not two"

    if "approved" in results:
        assert stored.status is MarketStatus.APPROVED
        assert stored.approved_by_id == approver.id
        assert stored.proposed_outcome_id is not None
        assert approvals == 1
    else:
        assert stored.status is MarketStatus.CLOSED
        assert stored.proposed_outcome_id is None
        assert stored.approved_by_id is None
        assert rejections == 1
