"""What reaches the audit log, and what does not. [4.3] #15, ADR 0006.

An admin action and its entry commit together or not at all. Entries are read
back as `audit_svc`, and each test filters on a fresh actor id, because the log
can never be cleaned.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import DraftIncomplete
from model.audit import AdminAction
from service import market_service
from service.audit import Actor

# Aliased so the names do not shadow an `actor` argument.
from unit_test.conftest import (
    CLOSE_REASON,
    CRITERIA,
    EVIDENCE_NOTE,
    EVIDENCE_URL,
    REJECTION_REASON,
    closed_market,
    proposed_before_ids,
    proposed_market,
    published_market,
)
from unit_test.conftest import actor as _actor
from unit_test.conftest import approval_request as _approval
from unit_test.conftest import close_request as _close
from unit_test.conftest import market_terms as _request
from unit_test.conftest import proposal_request as _proposal
from unit_test.conftest import rejection_request as _rejection

_ENTRIES = text(
    "SELECT * FROM audit.admin_actions WHERE actor_id = :actor "
    "ORDER BY occurred_at DESC, id DESC"
)


async def _entries(audit_reader: AsyncSession, actor: Actor) -> list:
    return list((await audit_reader.execute(_ENTRIES, {"actor": actor.id})).mappings())


async def _save(session: AsyncSession, actor: Actor, **overrides: object):
    from model.schemas import MarketDraftRequest  # noqa: PLC0415

    return await market_service.save(
        session, actor, MarketDraftRequest(**_request(**overrides))  # type: ignore[arg-type]
    )


# --- what gets recorded ---------------------------------------------------
async def test_a_submission_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """[4.3] #15's first criterion: actor, action type, target and timestamp."""
    actor = _actor()
    before = datetime.now(UTC)

    market, _, _ = await _save(session, actor, status="submitted")

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 1

    entry = entries[0]
    assert entry["actor_id"] == actor.id
    assert entry["action_type"] == AdminAction.MARKET_SUBMITTED.value
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_it_records_who_the_actor_was_rather_than_who_they_are(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The username and role are snapshots, which survive a rename or demotion. ADR 0003."""
    actor = _actor(username="ihsan_b", role="admin")

    await _save(session, actor, status="submitted")

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["actor_username"] == "ihsan_b"
    assert entry["actor_role"] == "admin"


async def test_it_records_the_terms_that_were_submitted(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The terms snapshot: the table keeps only the latest terms."""
    actor = _actor()

    await _save(
        session,
        actor,
        status="submitted",
        liquidity_b=Decimal("250"),
        seed_subsidy=Decimal("500"),
        question="Will the MAS core inflation print for December 2026 be below 2%?",
    )

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["target_label"].startswith("Will the MAS core inflation print")
    assert entry["context"]["liquidity_b"] == "250.0000"
    assert entry["context"]["seed_subsidy"] == "500.0000"
    assert entry["context"]["outcomes"] == ["Yes", "No"]
    assert entry["context"]["resolution_criteria"] == CRITERIA


async def test_the_subsidy_is_recorded_exactly(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """A string, not a float: the subsidy is money."""
    actor = _actor()

    await _save(session, actor, status="submitted", seed_subsidy=Decimal("1234.5678"))

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["context"]["seed_subsidy"] == "1234.5678"


async def test_each_submission_appends_rather_than_replacing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Re-submitting the same market leaves both entries, newest first."""
    actor = _actor()
    key = uuid.uuid4()

    await _save(session, actor, draft_key=key, status="submitted", question="First terms here")
    await _save(session, actor, draft_key=key, status="submitted", question="Second terms here")

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 2
    assert entries[0]["target_label"] == "Second terms here"
    assert entries[1]["target_label"] == "First terms here"


async def test_a_changed_resolution_rule_shows_in_the_snapshot(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The resolution rule is a term, so a change to it alone shows. ADR 0015."""
    actor = _actor()
    terms = _request(draft_key=uuid.uuid4(), status="submitted")
    changed = "Resolves YES on any MAS print below 2.0%, revised or not."

    await _save(session, actor, **terms)
    await _save(session, actor, **{**terms, "resolution_criteria": changed})

    newest, oldest = await _entries(audit_reader, actor)
    assert oldest["context"]["resolution_criteria"] == CRITERIA
    assert newest["context"]["resolution_criteria"] == changed

    moved = {k for k in newest["context"] if newest["context"][k] != oldest["context"][k]}
    assert moved == {"resolution_criteria"}


# --- publication [1.3] #3 -------------------------------------------------
async def _publish(session: AsyncSession, actor: Actor, **overrides: object):
    market, _, _ = await _save(session, actor, status="submitted", **overrides)
    return await market_service.publish(session, actor, market.id)


async def test_a_publication_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """[1.3] #3's third criterion."""
    actor = _actor()
    before = datetime.now(UTC)

    market = await _publish(session, actor)

    published = [
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_PUBLISHED.value
    ]
    assert len(published) == 1

    entry = published[0]
    assert entry["actor_id"] == actor.id
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_submitting_and_publishing_leave_two_distinct_entries(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Two decisions, two records. ADR 0008."""
    actor = _actor()

    await _publish(session, actor)

    entries = await _entries(audit_reader, actor)
    assert {e["action_type"] for e in entries} == {
        AdminAction.MARKET_SUBMITTED.value,
        AdminAction.MARKET_PUBLISHED.value,
    }
    assert len(entries) == 2


async def test_a_publication_records_the_terms_that_went_live(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Carried on the entry, not pointed at: a resubmission can change the terms."""
    actor = _actor()

    await _publish(
        session,
        actor,
        liquidity_b=Decimal("250"),
        seed_subsidy=Decimal("500"),
        question="Will the MAS core inflation print for December 2026 be below 2%?",
    )

    entry = next(
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_PUBLISHED.value
    )
    assert entry["target_label"].startswith("Will the MAS core inflation print")
    assert entry["context"]["liquidity_b"] == "250.0000"
    assert entry["context"]["seed_subsidy"] == "500.0000"
    assert entry["context"]["outcomes"] == ["Yes", "No"]
    assert entry["context"]["resolution_criteria"] == CRITERIA


async def test_the_two_snapshots_have_the_same_shape(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """An unchanged market gives identical terms, so the two entries compare."""
    actor = _actor()

    await _publish(session, actor)

    entries = await _entries(audit_reader, actor)
    submitted = next(
        e for e in entries if e["action_type"] == AdminAction.MARKET_SUBMITTED.value
    )
    published = next(
        e for e in entries if e["action_type"] == AdminAction.MARKET_PUBLISHED.value
    )
    assert published["context"] == submitted["context"]


async def test_the_publication_entry_and_the_status_commit_together(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """ADR 0006's claim: an entry visible to a second connection is committed."""
    actor = _actor()

    market = await _publish(session, actor)

    published = [
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_PUBLISHED.value
    ]
    assert len(published) == 1
    assert published[0]["target_id"] == market.id


# --- closing a market early [2.3] #7 --------------------------------------
async def _close_early(session: AsyncSession, actor: Actor, closer: Actor | None = None):
    """A published market, stopped by hand by `closer`, the creator by default."""
    market = await published_market(session, actor)
    return await market_service.close_early(
        session, closer or actor, market.id, _close()
    )


async def test_an_early_close_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """[2.3] #7's third criterion: the actor, and what they acted on."""
    actor = _actor()
    before = datetime.now(UTC)

    market = await _close_early(session, actor)

    closed = [
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_CLOSED_EARLY.value
    ]
    assert len(closed) == 1

    entry = closed[0]
    assert entry["actor_id"] == actor.id
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_an_early_close_records_the_reason(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The other half of the third criterion, and the reason's only copy. ADR 0014."""
    actor = _actor()

    await _close_early(session, actor)

    entry = next(
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_CLOSED_EARLY.value
    )
    assert entry["reason"] == CLOSE_REASON


async def test_an_early_close_records_the_closing_time_it_cut_short(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """What makes "early" mean anything; `audit_svc` cannot look it up. ADR 0014."""
    actor = _actor()

    market = await _close_early(session, actor)

    entry = next(
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_CLOSED_EARLY.value
    )
    assert entry["context"]["close_time"] == market.close_time.isoformat()
    assert entry["occurred_at"] < market.close_time


async def test_an_early_close_names_the_administrator_who_closed_it(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The accountability half of ADR 0014: the closer is named, not the creator."""
    owner, overseer = _actor(username="ernest_t"), _actor(username="ihsan_b")

    await _close_early(session, owner, closer=overseer)

    entry = next(
        e for e in await _entries(audit_reader, overseer)
        if e["action_type"] == AdminAction.MARKET_CLOSED_EARLY.value
    )
    assert entry["actor_id"] == overseer.id
    assert entry["actor_username"] == "ihsan_b"
    assert await _entries(audit_reader, owner) != []


async def test_an_automatic_close_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The clock is not an actor. ADR 0011."""
    actor = _actor()

    await closed_market(session, actor)

    assert AdminAction.MARKET_CLOSED_EARLY.value not in {
        e["action_type"] for e in await _entries(audit_reader, actor)
    }


async def test_a_refused_close_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """A close that raises takes its entry down with it."""
    from core.errors import CloseIncomplete  # noqa: PLC0415

    actor = _actor()
    market = await published_market(session, actor)

    with pytest.raises(CloseIncomplete):
        await market_service.close_early(session, actor, market.id, _close(reason="x"))

    await session.rollback()
    assert AdminAction.MARKET_CLOSED_EARLY.value not in {
        e["action_type"] for e in await _entries(audit_reader, actor)
    }


async def test_an_early_close_leaves_the_earlier_entries_alone(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Three decisions, three records, newest first."""
    actor = _actor()

    await _close_early(session, actor)

    entries = await _entries(audit_reader, actor)
    assert [e["action_type"] for e in entries] == [
        AdminAction.MARKET_CLOSED_EARLY.value,
        AdminAction.MARKET_PUBLISHED.value,
        AdminAction.MARKET_SUBMITTED.value,
    ]


# --- proposing an outcome [3.1] #9 ----------------------------------------
async def _propose(session: AsyncSession, actor: Actor, **overrides: object):
    market = await closed_market(session, actor)
    return await market_service.propose_outcome(
        session, actor, market.id, _proposal(market.outcomes[0].id, **overrides)
    )


async def test_a_proposal_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The record that outlives a rejection's clearing of the columns. ADR 0013."""
    actor = _actor()
    before = datetime.now(UTC)

    market = await _propose(session, actor)

    proposed = [
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_PROPOSED.value
    ]
    assert len(proposed) == 1

    entry = proposed[0]
    assert entry["actor_id"] == actor.id
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_a_proposal_records_the_outcome_and_the_evidence(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The label as well as the id: `audit_svc` can read nothing but the log."""
    actor = _actor()

    market = await _propose(session, actor)

    entry = next(
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_PROPOSED.value
    )
    assert entry["context"]["winning_outcome_id"] == str(market.proposed_outcome_id)
    assert entry["context"]["winning_outcome"] == "Yes"
    assert entry["context"]["evidence_url"] == EVIDENCE_URL
    assert entry["context"]["evidence_note"] == EVIDENCE_NOTE


async def test_the_evidence_is_context_rather_than_a_reason(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Evidence is not a reason. ADR 0013."""
    actor = _actor()

    await _propose(session, actor)

    entry = next(
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_PROPOSED.value
    )
    assert entry["reason"] is None


async def test_a_proposal_leaves_the_earlier_entries_alone(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Three decisions, three records; the automatic close is not one. ADR 0011."""
    actor = _actor()

    await _propose(session, actor)

    entries = await _entries(audit_reader, actor)
    assert [e["action_type"] for e in entries] == [
        AdminAction.MARKET_OUTCOME_PROPOSED.value,
        AdminAction.MARKET_PUBLISHED.value,
        AdminAction.MARKET_SUBMITTED.value,
    ]


async def test_a_refused_proposal_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """A proposal that raises takes its entry down with it."""
    from core.errors import ProposalIncomplete  # noqa: PLC0415

    actor = _actor()

    with pytest.raises(ProposalIncomplete):
        await _propose(session, actor, evidence_url=None, evidence_note=None)

    await session.rollback()
    entries = await _entries(audit_reader, actor)
    assert AdminAction.MARKET_OUTCOME_PROPOSED.value not in {
        e["action_type"] for e in entries
    }


# --- deciding a proposal [3.2] #10 ----------------------------------------
async def _approve(session: AsyncSession, proposer: Actor, approver: Actor):
    """A market proposed for by its creator, then approved by a second `approver`."""
    market = await proposed_market(session, proposer)
    return await market_service.approve_outcome(
        session, approver, market.id, _approval(market.proposal_id)
    )


async def _reject(
    session: AsyncSession, proposer: Actor, rejecter: Actor, **overrides: object
):
    market = await proposed_market(session, proposer)
    return await market_service.reject_outcome(
        session, rejecter, market.id, _rejection(market.proposal_id, **overrides)
    )


def _snapshot(
    proposer: Actor,
    proposal_id: uuid.UUID,
    winning_outcome_id: uuid.UUID,
    proposed_at: datetime,
) -> dict[str, object]:
    """The context both decision entries carry, for the default proposal."""
    return {
        "proposal_id": str(proposal_id),
        "winning_outcome_id": str(winning_outcome_id),
        "winning_outcome": "Yes",
        "evidence_url": EVIDENCE_URL,
        "evidence_note": EVIDENCE_NOTE,
        "proposed_by_id": str(proposer.id),
        "proposed_by_username": proposer.username,
        "proposed_at": proposed_at.isoformat(),
    }


async def test_an_approval_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The actor, and what they acted on."""
    proposer, approver = _actor(), _actor(username="ihsan_b")
    before = datetime.now(UTC)

    market = await _approve(session, proposer, approver)

    approved = [
        e for e in await _entries(audit_reader, approver)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_APPROVED.value
    ]
    assert len(approved) == 1

    entry = approved[0]
    assert entry["actor_id"] == approver.id
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["target_label"] == market.question
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_an_approval_names_the_approver_not_the_proposer(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The second signature is what this entry records. ADR 0016."""
    proposer, approver = _actor(username="ernest_t"), _actor(username="ihsan_b")

    await _approve(session, proposer, approver)

    entry = next(
        e for e in await _entries(audit_reader, approver)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_APPROVED.value
    )
    assert entry["actor_id"] == approver.id
    assert entry["actor_username"] == "ihsan_b"
    assert AdminAction.MARKET_OUTCOME_APPROVED.value not in {
        e["action_type"] for e in await _entries(audit_reader, proposer)
    }


async def test_an_approval_records_the_proposal_it_agreed_to(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """What was approved, on the entry: `audit_svc` cannot read the market. ADR 0016."""
    proposer, approver = _actor(username="ernest_t"), _actor(username="ihsan_b")

    market = await _approve(session, proposer, approver)

    entry = next(
        e for e in await _entries(audit_reader, approver)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_APPROVED.value
    )
    assert entry["context"] == _snapshot(
        proposer, market.proposal_id, market.proposed_outcome_id, market.proposed_at
    )


async def test_an_approval_has_no_reason(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """`reason IS NOT NULL` keeps meaning "somebody had to explain". ADR 0016."""
    proposer, approver = _actor(), _actor(username="ihsan_b")

    await _approve(session, proposer, approver)

    entry = next(
        e for e in await _entries(audit_reader, approver)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_APPROVED.value
    )
    assert entry["reason"] is None


async def test_a_rejection_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Under the rejecter, never the proposer."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    before = datetime.now(UTC)

    market = await _reject(session, proposer, rejecter)

    rejected = [
        e for e in await _entries(audit_reader, rejecter)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_REJECTED.value
    ]
    assert len(rejected) == 1

    entry = rejected[0]
    assert entry["actor_id"] == rejecter.id
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["target_label"] == market.question
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_a_rejection_records_the_reason(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """[3.2] #10's second criterion; the log is the reason's only copy."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")

    await _reject(session, proposer, rejecter)

    entry = next(
        e for e in await _entries(audit_reader, rejecter)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_REJECTED.value
    )
    assert entry["reason"] == REJECTION_REASON


async def test_a_rejection_records_the_proposal_it_cleared(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The snapshot is taken before the columns are cleared. ADR 0016."""
    proposer, rejecter = _actor(username="ernest_t"), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    market_id, proposal_id = market.id, market.proposal_id
    winner, proposed_at = market.proposed_outcome_id, market.proposed_at

    await market_service.reject_outcome(
        session, rejecter, market_id, _rejection(proposal_id)
    )

    session.expire_all()
    stored = await market_service.get(session, proposer.id, market_id)
    assert stored.proposed_outcome_id is None

    entry = next(
        e for e in await _entries(audit_reader, rejecter)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_REJECTED.value
    )
    assert entry["context"] == _snapshot(proposer, proposal_id, winner, proposed_at)


async def test_the_two_decision_entries_have_the_same_shape(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Both decisions carry the same keys, so a reader handles one shape."""
    proposer, decider = _actor(), _actor(username="ihsan_b")

    await _approve(session, proposer, decider)
    await _reject(session, proposer, decider)

    entries = await _entries(audit_reader, decider)
    approved = next(
        e for e in entries if e["action_type"] == AdminAction.MARKET_OUTCOME_APPROVED.value
    )
    rejected = next(
        e for e in entries if e["action_type"] == AdminAction.MARKET_OUTCOME_REJECTED.value
    )
    assert set(approved["context"]) == set(rejected["context"])


async def test_a_refused_decision_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """A decision refused for who asked or what they sent writes no entry."""
    from core.errors import RejectionIncomplete, SecondAdministratorRequired  # noqa: PLC0415

    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    pending = await proposed_market(session, proposer)
    market_id, proposal_id = pending.id, pending.proposal_id

    with pytest.raises(SecondAdministratorRequired):
        await market_service.approve_outcome(
            session, proposer, market_id, _approval(proposal_id)
        )
    await session.rollback()

    with pytest.raises(RejectionIncomplete):
        await market_service.reject_outcome(
            session, rejecter, market_id, _rejection(proposal_id, reason="x")
        )
    await session.rollback()

    decisions = {
        AdminAction.MARKET_OUTCOME_APPROVED.value,
        AdminAction.MARKET_OUTCOME_REJECTED.value,
    }
    for actor in (proposer, rejecter):
        recorded = {e["action_type"] for e in await _entries(audit_reader, actor)}
        assert recorded.isdisjoint(decisions)


async def test_a_decision_leaves_the_earlier_entries_alone(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Four decisions by two people, each under the name of whoever made it."""
    proposer, approver = _actor(), _actor(username="ihsan_b")

    await _approve(session, proposer, approver)

    assert [e["action_type"] for e in await _entries(audit_reader, proposer)] == [
        AdminAction.MARKET_OUTCOME_PROPOSED.value,
        AdminAction.MARKET_PUBLISHED.value,
        AdminAction.MARKET_SUBMITTED.value,
    ]
    assert [e["action_type"] for e in await _entries(audit_reader, approver)] == [
        AdminAction.MARKET_OUTCOME_APPROVED.value,
    ]


async def test_the_log_keeps_both_proposals_after_a_rejection(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The row holds the latest proposal; the log holds every one."""
    proposer, rejecter = _actor(), _actor(username="ihsan_b")
    market = await proposed_market(session, proposer)
    market_id, proposal_id, second = market.id, market.proposal_id, market.outcomes[1].id

    await market_service.reject_outcome(
        session, rejecter, market_id, _rejection(proposal_id)
    )
    await market_service.propose_outcome(session, proposer, market_id, _proposal(second))

    entries = await _entries(audit_reader, proposer)
    assert [e["action_type"] for e in entries] == [
        AdminAction.MARKET_OUTCOME_PROPOSED.value,
        AdminAction.MARKET_OUTCOME_PROPOSED.value,
        AdminAction.MARKET_PUBLISHED.value,
        AdminAction.MARKET_SUBMITTED.value,
    ]
    assert [e["context"]["winning_outcome"] for e in entries[:2]] == ["No", "Yes"]
    assert [e["action_type"] for e in await _entries(audit_reader, rejecter)] == [
        AdminAction.MARKET_OUTCOME_REJECTED.value,
    ]


async def test_a_decision_on_a_proposal_from_before_ids_records_a_null(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """JSON null, not "None": a reviewer quotes this value back."""
    proposer, approver = _actor(), _actor()
    market = await proposed_before_ids(session, proposer)

    await market_service.approve_outcome(
        session, approver, market.id, _approval_quoting_null()
    )

    [entry] = await _entries(audit_reader, approver)
    assert "proposal_id" in entry["context"]
    assert entry["context"]["proposal_id"] is None


def _approval_quoting_null():
    from model.schemas import OutcomeApprovalRequest  # noqa: PLC0415

    return OutcomeApprovalRequest(proposal_id=None)


async def test_a_decision_names_the_proposal_entry_it_decided(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Each decision quotes its proposal entry's id, not a timestamp to match. ADR 0016."""
    proposer, rejecter, approver = _actor(), _actor(), _actor()
    market = await proposed_market(session, proposer)
    market_id, first, second_winner = market.id, market.proposal_id, market.outcomes[1].id

    await market_service.reject_outcome(
        session, rejecter, market_id, _rejection(first)
    )
    replacement = await market_service.propose_outcome(
        session, proposer, market_id, _proposal(second_winner)
    )
    second = replacement.proposal_id
    await market_service.approve_outcome(
        session, approver, market_id, _approval(second)
    )

    proposed = [
        e["context"]["proposal_id"]
        for e in await _entries(audit_reader, proposer)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_PROPOSED.value
    ]
    [rejected] = await _entries(audit_reader, rejecter)
    [approved] = await _entries(audit_reader, approver)

    assert proposed == [str(second), str(first)]
    assert rejected["context"]["proposal_id"] == str(first)
    assert approved["context"]["proposal_id"] == str(second)


# --- what does not get recorded -------------------------------------------
async def test_an_autosave_is_not_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The log is for decisions, not keystrokes."""
    actor = _actor()

    for word in ("Will", "Will Singapore", "Will Singapore core inflation"):
        await _save(session, actor, question=word)

    assert await _entries(audit_reader, actor) == []


async def test_a_refused_publish_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Sixty days on the close time has passed: the publish adds no entry."""
    from datetime import timedelta as _timedelta  # noqa: PLC0415

    actor = _actor()
    market, _, _ = await _save(session, actor, status="submitted")

    with pytest.raises(DraftIncomplete):
        await market_service.publish(
            session, actor, market.id, now=datetime.now(UTC) + _timedelta(days=60)
        )

    await session.rollback()
    entries = await _entries(audit_reader, actor)
    assert [e["action_type"] for e in entries] == [AdminAction.MARKET_SUBMITTED.value]


async def test_publishing_someone_elses_market_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The 404 writes no entry naming who tried."""
    from core.errors import MarketNotFound  # noqa: PLC0415

    owner, intruder = _actor(), _actor()
    market, _, _ = await _save(session, owner, status="submitted")

    with pytest.raises(MarketNotFound):
        await market_service.publish(session, intruder, market.id)

    await session.rollback()
    assert await _entries(audit_reader, intruder) == []


async def test_a_refused_submission_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The atomicity claim ADR 0006 names: a refused action leaves no entry."""
    actor = _actor()

    with pytest.raises(DraftIncomplete):
        await _save(session, actor, status="submitted", close_time=None)

    await session.rollback()
    assert await _entries(audit_reader, actor) == []


async def test_the_entry_and_the_market_commit_together(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The other direction: an entry a second connection sees is committed."""
    actor = _actor()

    market, _, _ = await _save(session, actor, status="submitted")

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 1
    assert entries[0]["target_id"] == market.id


# --- the grants that make it append-only ----------------------------------
async def test_this_service_cannot_read_the_log_it_writes_to(
    session: AsyncSession,
) -> None:
    """Guard: INSERT without SELECT, so no service reads another's actions. ADR 0006."""
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT count(*) FROM audit.admin_actions"))
    await session.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.admin_actions SET reason = 'edited'",
        "DELETE FROM audit.admin_actions",
        "TRUNCATE audit.admin_actions",
    ],
)
async def test_this_service_cannot_change_the_log(
    session: AsyncSession, statement: str
) -> None:
    """[4.3] #15's second criterion, one layer below the API: append-only for real."""
    with pytest.raises(ProgrammingError):
        await session.execute(text(statement))
    await session.rollback()
