"""Proposing an outcome, driven through the service layer without HTTP. [3.1] #9.

Every market is closed the way production closes one, by the clock and the
sweep, because the first criterion is about the CLOSED boundary. HTTP answers
are in unit_test/controller, audit entries in test_audit.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    MarketNotClosed,
    MarketNotFound,
    MarketPendingResolution,
    ProposalIncomplete,
)
from model.entities import Market, MarketStatus
from service import market_service
from service.validation import MIN_EVIDENCE_NOTE_LENGTH

# Aliased so the names do not shadow an `actor` argument.
from unit_test.conftest import (
    EVIDENCE_NOTE,
    EVIDENCE_URL,
    closed_market,
    overdue_market,
    published_market,
)
from unit_test.conftest import actor as _actor
from unit_test.conftest import draft_request as _request
from unit_test.conftest import proposal_request as _proposal

# Read back as audit_svc: market_svc holds INSERT on this table and no SELECT.
_PROPOSED_ENTRIES = text(
    "SELECT id FROM audit.admin_actions "
    "WHERE actor_id = :actor AND action_type = 'market.outcome_proposed'"
)


def _winner(market: Market) -> uuid.UUID:
    return market.outcomes[0].id


# --- the transition -------------------------------------------------------
async def test_a_closed_market_takes_a_proposal(session: AsyncSession) -> None:
    """[3.1] #9's third criterion: the status moves to PENDING_RESOLUTION."""
    actor = _actor()
    market = await closed_market(session, actor)

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market))
    )

    assert proposed.status is MarketStatus.PENDING_RESOLUTION
    assert proposed.proposed_outcome_id == _winner(market)
    assert proposed.proposed_at is not None


async def test_the_proposal_survives_a_reload(session: AsyncSession) -> None:
    """Read from the database, so this fails if the transition is never committed."""
    actor = _actor()
    market = await closed_market(session, actor)
    winner = _winner(market)

    await market_service.propose_outcome(session, actor, market.id, _proposal(winner))

    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.PENDING_RESOLUTION
    assert stored.proposed_outcome_id == winner


async def test_the_proposer_is_stored_on_the_market(session: AsyncSession) -> None:
    """[3.1] #9's fourth criterion: the id, and a snapshotted name. ADR 0013."""
    actor = _actor(username="ihsan_b")
    market = await closed_market(session, actor)

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market))
    )

    assert proposed.proposed_by_id == actor.id
    assert proposed.proposed_by_username == "ihsan_b"


async def test_the_evidence_is_stored(session: AsyncSession) -> None:
    """[3.1] #9's second criterion, in the half that outlives the request."""
    actor = _actor()
    market = await closed_market(session, actor)

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market))
    )

    assert proposed.proposal_evidence_url == EVIDENCE_URL
    assert proposed.proposal_evidence_note == EVIDENCE_NOTE


async def test_proposing_records_when_it_happened(session: AsyncSession) -> None:
    """A timestamp of its own, like `submitted_at` and `published_at`."""
    actor = _actor()
    market = await closed_market(session, actor)
    later = datetime.now(UTC) + timedelta(days=2)

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market)), now=later
    )

    assert proposed.proposed_at == later
    assert proposed.closed_at != proposed.proposed_at


async def test_a_proposal_leaves_the_terms_alone(session: AsyncSession) -> None:
    """The approver is asked about the market traders traded. ADR 0013."""
    actor = _actor()
    market = await closed_market(session, actor, question="The question traders saw?")
    close_time = market.close_time

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market))
    )

    assert proposed.question == "The question traders saw?"
    assert proposed.close_time == close_time
    assert [o.label for o in proposed.outcomes] == ["Yes", "No"]


# --- which markets may be proposed for ------------------------------------
async def test_an_open_market_cannot_be_proposed_for(session: AsyncSession) -> None:
    """[3.1] #9's first criterion: no answer while people are still betting."""
    actor = _actor()
    market = await published_market(session, actor)

    with pytest.raises(MarketNotClosed):
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(_winner(market))
        )


@pytest.mark.parametrize("status", ["draft", "submitted"])
async def test_a_market_that_never_opened_cannot_be_proposed_for(
    session: AsyncSession, status: str
) -> None:
    """Nothing to resolve, and the same remedy: this market has not finished."""
    actor = _actor()
    market, _, _ = await market_service.save(session, actor, _request(status=status))

    with pytest.raises(MarketNotClosed):
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(_winner(market))
        )


async def test_a_market_past_its_close_time_but_unswept_is_refused(
    session: AsyncSession,
) -> None:
    """Guard: this gate reads the status, so it waits for the sweep. The safe
    direction; do not "fix" it to derive. ADR 0013."""
    actor = _actor()
    market = await overdue_market(session, actor)
    assert market.status is MarketStatus.OPEN

    with pytest.raises(MarketNotClosed):
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(_winner(market))
        )


async def test_the_second_proposal_does_not_overwrite_the_first(
    session: AsyncSession,
) -> None:
    """A second proposal is refused and the first stays on the row. ADR 0013."""
    actor = _actor()
    market = await closed_market(session, actor)
    first, second = market.outcomes[0].id, market.outcomes[1].id
    await market_service.propose_outcome(session, actor, market.id, _proposal(first))

    with pytest.raises(MarketPendingResolution):
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(second)
        )

    await session.rollback()
    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.proposed_outcome_id == first


async def test_another_administrators_market_is_left_alone(
    session: AsyncSession,
) -> None:
    """Another administrator gets a 404, and the refusal writes nothing. ADR 0013."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(MarketNotFound):
        await market_service.propose_outcome(
            session, _actor(), market.id, _proposal(_winner(market))
        )

    await session.rollback()
    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.CLOSED
    assert stored.proposed_by_id is None


# --- the winning outcome --------------------------------------------------
async def test_an_outcome_from_another_market_is_refused(
    session: AsyncSession,
) -> None:
    """The check a foreign key could not make. ADR 0013."""
    actor = _actor()
    # Read now: `closed_market` expires the session, and a later `mine.id`
    # would lazy-load outside the greenlet.
    mine_id = (await closed_market(session, actor)).id
    theirs = _winner(await closed_market(session, actor))

    with pytest.raises(ProposalIncomplete) as raised:
        await market_service.propose_outcome(
            session, actor, mine_id, _proposal(theirs)
        )

    assert [p.field for p in raised.value.problems] == ["winning_outcome_id"]


# --- the evidence ---------------------------------------------------------
async def test_a_url_alone_is_enough(session: AsyncSession) -> None:
    """[3.1] #9's second criterion is a URL *or* a note."""
    actor = _actor()
    market = await closed_market(session, actor)

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market), evidence_note=None)
    )

    assert proposed.proposal_evidence_url == EVIDENCE_URL
    assert proposed.proposal_evidence_note is None


async def test_a_note_alone_is_enough(session: AsyncSession) -> None:
    """For a source that is not a link."""
    actor = _actor()
    market = await closed_market(session, actor)

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market), evidence_url=None)
    )

    assert proposed.proposal_evidence_url is None
    assert proposed.proposal_evidence_note == EVIDENCE_NOTE


async def test_neither_is_refused(session: AsyncSession) -> None:
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(ProposalIncomplete) as raised:
        await market_service.propose_outcome(
            session,
            actor,
            market.id,
            _proposal(_winner(market), evidence_url=None, evidence_note=None),
        )

    assert [p.field for p in raised.value.problems] == ["evidence"]


async def test_a_url_that_is_not_a_url_is_refused(session: AsyncSession) -> None:
    """Named on its own field and on the pair, so fixing the URL is enough."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(ProposalIncomplete) as raised:
        await market_service.propose_outcome(
            session,
            actor,
            market.id,
            _proposal(_winner(market), evidence_url="mas.gov.sg", evidence_note=None),
        )

    assert [p.field for p in raised.value.problems] == ["evidence_url", "evidence"]


async def test_a_broken_url_beside_a_usable_note_still_names_the_url(
    session: AsyncSession,
) -> None:
    """The note satisfies the pair, but a dead link must not be stored."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(ProposalIncomplete) as raised:
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(_winner(market), evidence_url="nope")
        )

    assert [p.field for p in raised.value.problems] == ["evidence_url"]


async def test_a_note_too_short_to_document_anything_is_refused(
    session: AsyncSession,
) -> None:
    """"ok" satisfies "a note was given" and documents nothing."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(ProposalIncomplete) as raised:
        await market_service.propose_outcome(
            session,
            actor,
            market.id,
            _proposal(_winner(market), evidence_url=None, evidence_note="ok"),
        )

    assert [p.field for p in raised.value.problems] == ["evidence_note", "evidence"]


async def test_the_note_minimum_is_counted_after_stripping(
    session: AsyncSession,
) -> None:
    """Spaces must not stretch a short note past the floor."""
    actor = _actor()
    market = await closed_market(session, actor)
    padded = "    short    "
    assert len(padded) >= MIN_EVIDENCE_NOTE_LENGTH > len(padded.strip())

    with pytest.raises(ProposalIncomplete) as raised:
        await market_service.propose_outcome(
            session,
            actor,
            market.id,
            _proposal(_winner(market), evidence_url=None, evidence_note=padded),
        )

    assert [p.field for p in raised.value.problems] == ["evidence_note", "evidence"]


async def test_every_problem_is_reported_at_once(session: AsyncSession) -> None:
    """One pass, not one save per mistake."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(ProposalIncomplete) as raised:
        await market_service.propose_outcome(
            session,
            actor,
            market.id,
            _proposal(uuid.uuid4(), evidence_url="nope", evidence_note=None),
        )

    assert {p.field for p in raised.value.problems} == {
        "winning_outcome_id",
        "evidence_url",
        "evidence",
    }


async def test_a_refused_proposal_leaves_the_market_closed(
    session: AsyncSession,
) -> None:
    """All-or-nothing, the same as a refused submission."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(ProposalIncomplete):
        await market_service.propose_outcome(
            session,
            actor,
            market.id,
            _proposal(_winner(market), evidence_url=None, evidence_note=None),
        )

    await session.rollback()
    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.CLOSED
    assert stored.proposed_at is None
    assert stored.proposed_by_id is None


# --- a proposal freezes the market ----------------------------------------
async def test_a_late_autosave_cannot_touch_a_market_awaiting_resolution(
    session: AsyncSession,
) -> None:
    """A save would change the terms the proposal is about. ADR 0013."""
    actor = _actor()
    key = uuid.uuid4()
    market = await closed_market(session, actor, draft_key=key)
    await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market))
    )

    with pytest.raises(MarketPendingResolution):
        await market_service.save(session, actor, _request(draft_key=key, question="Edited"))


async def test_a_market_awaiting_resolution_cannot_be_published(
    session: AsyncSession,
) -> None:
    """The refusal names the state it is in, not "not submitted"."""
    actor = _actor()
    market = await closed_market(session, actor)
    await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market))
    )

    with pytest.raises(MarketPendingResolution):
        await market_service.publish(session, actor, market.id)


# --- two requests at once -------------------------------------------------
async def test_two_concurrent_proposals_produce_one_winner(
    clean_database, audit_reader: AsyncSession
) -> None:
    """A double-clicked propose: two real transactions, one proposal in the log.

    The loser blocks on the row lock, re-reads PENDING_RESOLUTION and is
    refused. ADR 0015.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    factory = get_session_factory()
    actor = _actor()

    async with factory() as setup:
        market = await closed_market(setup, actor)
        market_id, winner = market.id, _winner(market)

    async def attempt() -> str:
        async with factory() as own:
            try:
                await market_service.propose_outcome(
                    own, actor, market_id, _proposal(winner)
                )
            except MarketPendingResolution:
                return "refused"
            return "proposed"

    results = await asyncio.gather(attempt(), attempt())

    assert sorted(results) == ["proposed", "refused"]

    entries = list(
        (await audit_reader.execute(_PROPOSED_ENTRIES, {"actor": actor.id})).mappings()
    )
    assert len(entries) == 1, "the log must record one proposal, not two"
