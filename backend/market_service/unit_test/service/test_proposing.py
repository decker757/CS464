"""Proposing an outcome, driven through the service layer without HTTP. [3.1] #9.

The transition and the rules guarding it. Status codes and the error envelope
live in unit_test/controller; what reaches the audit log lives in
unit_test/service/test_audit.py.

Every market here is closed the way a real one closes — published, then its
close time moved into the past, then swept. That costs three extra lines per
fixture and is worth them: this ticket's first acceptance criterion is about
the boundary between CLOSED and everything else, and a test that reached CLOSED
by assigning to `status` would be asserting against a state no market in
production arrives in.
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
from service.audit import Actor

# The suite's actor factory and market builders. Aliased rather than imported
# under their own names because every helper below takes an `actor` argument,
# which would shadow the first.
from unit_test.conftest import (
    EVIDENCE_NOTE,
    EVIDENCE_URL,
    closed_market,
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


async def _overdue(session: AsyncSession, actor: Actor, **overrides: object) -> Market:
    """A published market whose closing time has passed, with no sweep yet.

    The window ADR 0011 describes, and the one rung of the ladder that only
    this suite cares about: trading has genuinely stopped, because `close_time`
    is what stops it, and `status` still says `open` because the sweeper has
    not run.
    """
    market = await published_market(session, actor, **overrides)
    market.close_time = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    return market


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
    """Asserted from the database rather than the in-memory object, so this
    fails if the transition is never actually committed."""
    actor = _actor()
    market = await closed_market(session, actor)
    winner = _winner(market)

    await market_service.propose_outcome(session, actor, market.id, _proposal(winner))

    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.PENDING_RESOLUTION
    assert stored.proposed_outcome_id == winner


async def test_the_proposer_is_stored_on_the_market(session: AsyncSession) -> None:
    """[3.1] #9's fourth criterion, and the value [3.2] #10 compares against to
    disable the approve control for the administrator who proposed.

    The username is stored beside the id because this service cannot resolve
    one from the other — there is no grant on `auth.users` and never will be
    (ADR 0003) — so a name not written here is a name nobody can render later.
    """
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
    """A timestamp of its own, like `submitted_at` and `published_at`.

    A market can sit closed for a week before anybody gets round to proposing,
    and [3.3] #11's dispute window will need to know how long this one has been
    waiting on a second administrator.
    """
    actor = _actor()
    market = await closed_market(session, actor)
    later = datetime.now(UTC) + timedelta(days=2)

    proposed = await market_service.propose_outcome(
        session, actor, market.id, _proposal(_winner(market)), now=later
    )

    assert proposed.proposed_at == later
    assert proposed.closed_at != proposed.proposed_at


async def test_a_proposal_leaves_the_terms_alone(session: AsyncSession) -> None:
    """The request names an outcome and evidence and nothing else, so the market
    a second administrator is asked to approve is the market traders traded."""
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
    """[3.1] #9's first criterion.

    Proposing an outcome for a market people can still trade announces the
    answer to a question they are still betting on — the same failure
    `service/validation.py`'s close-before-resolution rule exists to prevent.
    """
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
    """Nobody traded it, so there is nothing to resolve. It is still the wrong
    state rather than a different error, because the remedy is the same: this
    market has not finished."""
    actor = _actor()
    market, _, _ = await market_service.save(session, actor, _request(status=status))

    with pytest.raises(MarketNotClosed):
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(_winner(market))
        )


async def test_a_market_past_its_close_time_but_unswept_is_refused(
    session: AsyncSession,
) -> None:
    """The window ADR 0011 leaves open, asserted rather than assumed.

    Trading has stopped — `close_time` has passed and the trade path derives
    that — but the sweeper has not written `closed` yet, so this gate says no.
    That is the safe direction of the error: a propose control that appears a
    few seconds late, rather than a proposal on a market that is still live.
    Nobody notices, because `resolution_time` is necessarily after
    `close_time`, so an administrator with an answer is not standing on the
    closing bell.
    """
    actor = _actor()
    market = await _overdue(session, actor)
    assert market.status is MarketStatus.OPEN

    with pytest.raises(MarketNotClosed):
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(_winner(market))
        )


async def test_proposing_twice_is_refused(session: AsyncSession) -> None:
    """A second proposal would silently replace the thing a second
    administrator is being asked to agree to. [3.2] #10's rejection is the way
    back to CLOSED, not another propose."""
    actor = _actor()
    market = await closed_market(session, actor)
    winner = _winner(market)
    await market_service.propose_outcome(session, actor, market.id, _proposal(winner))

    with pytest.raises(MarketPendingResolution):
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(winner)
        )


async def test_the_second_proposal_does_not_overwrite_the_first(
    session: AsyncSession,
) -> None:
    """The refusal above must actually protect the row, not merely report."""
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


async def test_another_administrator_cannot_propose(session: AsyncSession) -> None:
    """404, not 403, and for the same reason reading it is: a 403 would confirm
    the market exists. Resolution stays with the creator, like every other route
    in this service — [3.2] #10 is the ticket that widens it, because its own
    acceptance criteria require a second administrator."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(MarketNotFound):
        await market_service.propose_outcome(
            session, _actor(), market.id, _proposal(_winner(market))
        )


async def test_another_administrators_market_is_left_alone(
    session: AsyncSession,
) -> None:
    """The refusal above must not be a refusal that also wrote something."""
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


async def test_proposing_for_a_market_that_does_not_exist_is_not_found(
    session: AsyncSession,
) -> None:
    with pytest.raises(MarketNotFound):
        await market_service.propose_outcome(
            session, _actor(), uuid.uuid4(), _proposal(uuid.uuid4())
        )


# --- the winning outcome --------------------------------------------------
async def test_an_outcome_from_another_market_is_refused(
    session: AsyncSession,
) -> None:
    """The check a foreign key could not make.

    Outcome ids are unique across every market, so an FK on this column would
    accept another market's "Yes" without complaint. What matters is that the
    winner belongs to *this* market, and [3.4] #12 pays out against it.
    """
    actor = _actor()
    # Read out before the second market is built: `_closed` expires the session
    # to see the sweep's bulk UPDATE, which would turn a later `mine.id` into a
    # lazy refresh outside the greenlet.
    mine_id = (await closed_market(session, actor)).id
    theirs = _winner(await closed_market(session, actor))

    with pytest.raises(ProposalIncomplete) as raised:
        await market_service.propose_outcome(
            session, actor, mine_id, _proposal(theirs)
        )

    assert [p.field for p in raised.value.problems] == ["winning_outcome_id"]


async def test_an_outcome_that_does_not_exist_is_refused(
    session: AsyncSession,
) -> None:
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(ProposalIncomplete):
        await market_service.propose_outcome(
            session, actor, market.id, _proposal(uuid.uuid4())
        )


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
    """The case a URL cannot cover: the source was a phone call, a screenshot,
    or a judgement about an ambiguous print."""
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


async def test_blank_evidence_is_the_same_as_none(session: AsyncSession) -> None:
    """A cleared textarea sends an empty string, not a null."""
    actor = _actor()
    market = await closed_market(session, actor)

    with pytest.raises(ProposalIncomplete):
        await market_service.propose_outcome(
            session,
            actor,
            market.id,
            _proposal(_winner(market), evidence_url="   ", evidence_note=""),
        )


async def test_a_url_that_is_not_a_url_is_refused(session: AsyncSession) -> None:
    """Named on its own field as well as on the pair, so the administrator can
    see that fixing the URL is enough and they need not also write a note."""
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
    """The note satisfies "at least one", so the pair is not reported — but a
    value the administrator typed and that nobody can open is still wrong, and
    silently storing it would put a dead link in front of [3.3] #11's
    disputer."""
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
    """"ok" satisfies "a note was given" and documents nothing, which is the
    whole of what this story asks for."""
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


async def test_every_problem_is_reported_at_once(session: AsyncSession) -> None:
    """One pass, not one save per mistake. The same contract a refused
    submission already has."""
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
    """All-or-nothing, the same as a refused submission. A market half-way to
    pending resolution is a market nobody can reason about."""
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
    """The form may still be open behind everything that has happened since.

    A save here would change the terms the proposal was made against, which are
    the terms a second administrator is about to be asked to agree to.
    """
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
    """The refusal names the state it is actually in, rather than reporting
    that it is not submitted."""
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
    """An administrator double-clicks the propose button.

    Two real transactions, not a simulated race. Read without a lock both would
    see CLOSED, both would write their own outcome over the other's, and the
    log would carry two entries proposing different winners for one market.

    `propose_outcome` takes a row lock, so under READ COMMITTED the loser
    blocks on the SELECT and then re-reads the row the winner committed. It
    finds PENDING_RESOLUTION and is refused.
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
