"""Approving or rejecting a proposed outcome, through the service layer. [3.2] #10.

The proposer may never decide, compared by id; the refusals come in a fixed
order (state, identity, which proposal, reason); and each decision happens
once, which the real races at the bottom hold. ADR 0015, ADR 0016. HTTP
answers are in unit_test/controller, audit entries in test_audit.py.
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

# Aliased so the names do not shadow an `actor` argument.
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

# The seven proposal columns: an approval keeps them all, a rejection clears them all.
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
    """The one market in the database, re-read from Postgres."""
    session.expire_all()
    return (await session.execute(select(Market))).scalar_one()


async def _decide(
    session: AsyncSession,
    actor: Actor,
    market_id: uuid.UUID,
    decision: str,
    proposal_id: uuid.UUID | None,
) -> Market:
    """Approve, or reject with a usable reason, for the rules the two share."""
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
    """A pending market whose close time is still ahead, so only the status can
    refuse a trade; a clock-closed market would pass the trading tests vacuously."""
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
    """Read from the database, so this fails if the transition is never committed."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.approve_outcome(session, approver, market.id, _approve(market.proposal_id))

    stored = await _stored(session)
    assert stored.status is MarketStatus.APPROVED
    assert stored.approved_by_id == approver.id


async def test_both_identities_are_stored_on_the_market(session: AsyncSession) -> None:
    """[3.2] #10's third criterion: who proposed and who approved, on the row."""
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
    """Its own timestamp: [3.3] #11's dispute window will run from it."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    later = datetime.now(UTC) + timedelta(days=2)

    approved = await market_service.approve_outcome(
        session, approver, market.id, _approve(market.proposal_id), now=later
    )

    assert approved.approved_at == later
    assert approved.approved_at != approved.proposed_at


async def test_an_approval_keeps_the_proposal(session: AsyncSession) -> None:
    """The approved winner stays where it was; [3.4] #12 settles against it."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    before = _columns(market, _PROPOSAL_COLUMNS)
    assert None not in before.values()

    await market_service.approve_outcome(session, approver, market.id, _approve(market.proposal_id))

    assert _columns(await _stored(session), _PROPOSAL_COLUMNS) == before


async def test_an_approval_leaves_the_terms_and_closed_at_alone(
    session: AsyncSession,
) -> None:
    """Nothing but the decision moves."""
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
    """[3.2] #10's second criterion: back to CLOSED, ready to propose again."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    rejected = await market_service.reject_outcome(
        session, rejecter, market.id, _reject(market.proposal_id)
    )

    assert rejected.status is MarketStatus.CLOSED


async def test_the_rejection_survives_a_reload(session: AsyncSession) -> None:
    """Read from the database, so this fails if the transition is never committed."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.reject_outcome(session, rejecter, market.id, _reject(market.proposal_id))

    stored = await _stored(session)
    assert stored.status is MarketStatus.CLOSED
    assert stored.proposed_outcome_id is None


async def test_a_rejection_clears_every_proposal_column(session: AsyncSession) -> None:
    """All seven, not only the winner; the log keeps the rejected proposal. ADR 0016."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.reject_outcome(session, rejecter, market.id, _reject(market.proposal_id))

    stored = await _stored(session)
    assert _columns(stored, _PROPOSAL_COLUMNS) == dict.fromkeys(_PROPOSAL_COLUMNS)


async def test_a_rejection_leaves_closed_at_alone(session: AsyncSession) -> None:
    """`closed_at` says when trading stopped, which a rejection does not change."""
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
    """The rejecter is named in the log and nowhere else. ADR 0016."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)

    await market_service.reject_outcome(session, rejecter, market.id, _reject(market.proposal_id))

    stored = await _stored(session)
    assert _columns(stored, _APPROVER_COLUMNS) == dict.fromkeys(_APPROVER_COLUMNS)


async def test_a_rejection_leaves_the_terms_alone(session: AsyncSession) -> None:
    """The reason is about the proposal, not the market."""
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
    """Guard: the reason lives in the log only, not a `rejection_reason` column. ADR 0016."""
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
    """The point of sending it back to CLOSED: its creator proposes again."""
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
    """Back to CLOSED, not OPEN: nobody may bet on a question just answered."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await _stopped_early_and_proposed(session, proposer)

    rejected = await market_service.reject_outcome(
        session, rejecter, market.id, _reject(market.proposal_id)
    )

    assert closing.is_open_for_trading(rejected) is False


# --- who may decide -------------------------------------------------------
async def test_the_proposer_cannot_approve(session: AsyncSession) -> None:
    """[3.2] #10's first criterion: the rule behind the disabled button."""
    proposer = _actor()
    market = await proposed_market(session, proposer)

    with pytest.raises(SecondAdministratorRequired):
        await market_service.approve_outcome(session, proposer, market.id, _approve(market.proposal_id))


async def test_the_proposer_cannot_reject(session: AsyncSession) -> None:
    """Rejecting your own proposal is the un-propose ADR 0013 declined."""
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
    """Usernames can be reissued; a namesake is a legitimate second administrator."""
    proposer = _actor(username="ernest_t")
    market = await proposed_market(session, proposer)
    namesake = Actor(id=uuid.uuid4(), username=proposer.username, role="admin")

    approved = await market_service.approve_outcome(session, namesake, market.id, _approve(market.proposal_id))

    assert approved.status is MarketStatus.APPROVED


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_the_proposer_under_a_new_username_is_still_refused(
    session: AsyncSession, decision: str
) -> None:
    """The other direction: a rename does not make the proposer somebody else."""
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
    """Unscoped by necessity: the creator proposes and may not decide. ADR 0016."""
    owner, overseer = _actor(username="ernest_t"), _actor(username="ihsan_b")
    market = await proposed_market(session, owner)

    decided = await _decide(session, overseer, market.id, decision, market.proposal_id)

    assert decided.status is status
    assert decided.creator_id == owner.id


# --- which proposal is being decided ---------------------------------------
async def _replaced(
    session: AsyncSession, proposer: Actor, rejecter: Actor
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A market whose first proposal was rejected and replaced by a second.

    Returns (market id, stale first proposal id, current id, second winner) as
    plain values, because the callers roll back and an expired entity is
    unreadable.
    """
    first = await proposed_market(session, proposer)
    market_id, stale, second_winner = first.id, first.proposal_id, first.outcomes[1].id

    await market_service.reject_outcome(session, rejecter, market_id, _reject(stale))
    second = await market_service.propose_outcome(
        session, proposer, market_id, _proposal(second_winner)
    )
    return market_id, stale, second.proposal_id, second_winner


async def test_a_replacement_proposal_gets_a_new_id(session: AsyncSession) -> None:
    """Even for the same creator on the same market: the id alone tells them apart."""
    _, stale, current, _ = await _replaced(
        session, _actor(), _actor(username="ihsan_b")
    )

    assert current is not None
    assert current != stale


async def test_a_stale_approval_does_not_approve_the_replacement(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """A reviewer still reading proposal 1 approves after 1 was replaced by 2.

    No row lock can catch this; the quoted id does. ADR 0016.
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
    """The same stale page, the other button: a reason about 1 must not clear 2."""
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
    """A reviewer who reloads and quotes proposal 2 decides it."""
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
    """Identity before which proposal: reloading would not help the proposer."""
    proposer = _actor()
    market_id, stale, _, _ = await _replaced(
        session, proposer, _actor(username="ihsan_b")
    )

    with pytest.raises(SecondAdministratorRequired):
        await _decide(session, proposer, market_id, decision, stale)


async def test_a_stale_rejection_is_refused_before_its_reason_is_read(
    session: AsyncSession,
) -> None:
    """Which proposal before the reason."""
    market_id, stale, _, _ = await _replaced(
        session, _actor(), _actor(username="ihsan_b")
    )

    with pytest.raises(ProposalSuperseded):
        await market_service.reject_outcome(
            session, _actor(), market_id, _reject(stale, reason="x")
        )


# A proposal older than ids is decided by quoting null, and a null must open
# nothing else. ADR 0016.
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
    """The migration's promise: a proposal from before 0006 can still be decided."""
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
    """A null is not a wildcard."""
    market = await proposed_market(session, _actor())

    with pytest.raises(ProposalSuperseded):
        await _decide_quoting_null(
            session, _actor(username="ihsan_b"), market.id, decision
        )


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_a_stale_null_does_not_decide_the_replacement_of_an_old_proposal(
    session: AsyncSession, decision: str
) -> None:
    """The replaced-proposal case starting from an old proposal: a stale null is refused."""
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
    """State before which proposal: the market is past deciding."""
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
    """No proposal on it: a 409, not a 404, even to another admin. ADR 0016."""
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
    """The closest miss: CLOSED is the state just before a proposal."""
    owner = _actor()
    market = await closed_market(session, owner)

    with pytest.raises(MarketNotPendingResolution):
        await _decide(session, _actor(username="ihsan_b"), market.id, decision, market.proposal_id)


async def test_a_second_approval_does_not_overwrite_the_first(
    session: AsyncSession,
) -> None:
    """The refusal must protect the row; a later clock and another admin would show."""
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
    """An approval is final here; undoing one is [3.3] #11's dispute."""
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
    """After the first, there is no proposal left to reject."""
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
    """State before identity: another administrator already has."""
    proposer = _actor()
    market = await proposed_market(session, proposer)
    await market_service.approve_outcome(session, _actor(username="ihsan_b"), market.id, _approve(market.proposal_id))

    with pytest.raises(MarketAlreadyApproved):
        await _decide(session, proposer, market.id, decision, market.proposal_id)


async def test_the_identity_is_checked_before_the_reason(session: AsyncSession) -> None:
    """A proposer is not told to improve a rejection they may not make."""
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
    """Enforced here; the rules are in test_validation.py."""
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
    """All-or-nothing: the proposal is not cleared by a refused rejection."""
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
    """A market with nothing to reject is not told to fix its wording."""
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
    """Without the frozen check, the open form would write DRAFT over APPROVED."""
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
    """The refusal names the state it is in, not "not submitted"."""
    proposer = _actor()
    market = await _approved(session, proposer)

    with pytest.raises(MarketAlreadyApproved):
        await market_service.publish(session, proposer, market.id)


async def test_an_approved_market_cannot_be_proposed_for_again(
    session: AsyncSession,
) -> None:
    """Not `market_not_closed`, which would have the creator wait forever."""
    proposer = _actor()
    market = await _approved(session, proposer)

    with pytest.raises(MarketAlreadyApproved):
        await market_service.propose_outcome(
            session, proposer, market.id, _proposal(market.outcomes[1].id)
        )


async def test_an_approved_market_cannot_be_closed_early(session: AsyncSession) -> None:
    """Not `market_closed`, which is true and useless here."""
    proposer = _actor()
    market = await _approved(session, proposer)

    with pytest.raises(MarketAlreadyApproved):
        await market_service.close_early(session, _actor(), market.id, _close())


async def test_an_approved_market_is_not_tradeable(session: AsyncSession) -> None:
    """Both forms of the trading rule, with the close time still ahead."""
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
    """The sweep never writes CLOSED over APPROVED when the close time arrives."""
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
    """A double-clicked approve: two real transactions, one approval in the log.

    The loser blocks on the row lock, re-reads APPROVED and is refused. ADR 0015.
    """
    factory = get_session_factory()
    proposer, approver = _actor(), _actor(username="ihsan_b")

    async with factory() as setup:
        pending = await proposed_market(setup, proposer)
        market_id, proposal_id = pending.id, pending.proposal_id

    # Both connected before either starts, so the lock decides. ADR 0015.
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
    """Two administrators reject at once: one rejection in the log. ADR 0015."""
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
    """Two administrators disagree at once: exactly one decision, whichever wins."""
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
