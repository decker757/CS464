"""Publishing a market, driven through the service layer without HTTP. [1.3] #3.

The transition itself and the rules guarding it. What the publish endpoint
answers with lives in unit_test/controller, and what reaches the audit log
lives in unit_test/service/test_audit.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    DraftIncomplete,
    MarketAlreadyOpen,
    MarketNotEditable,
    MarketNotFound,
    MarketNotSubmitted,
)
from model.entities import Market, MarketStatus
from service import market_service
from service.audit import Actor

# The suite's one actor factory and one market builder. Aliased rather than
# imported under their own names because every helper below takes an `actor`
# argument, which would shadow the first.
from unit_test.conftest import actor as _actor
from unit_test.conftest import draft_request as _request


# Read back as audit_svc: market_svc holds INSERT on this table and no SELECT.
_PUBLISHED_ENTRIES = text(
    "SELECT id, target_label FROM audit.admin_actions "
    "WHERE actor_id = :actor AND action_type = 'market.published'"
)


async def _submitted(session: AsyncSession, actor: Actor, **overrides: object) -> Market:
    market, _, _ = await market_service.save(
        session, actor, _request(status="submitted", **overrides)
    )
    return market


# --- the transition -------------------------------------------------------
async def test_a_submitted_market_publishes(session: AsyncSession) -> None:
    """[1.3] #3's second criterion, in the half this service owns: the status
    moves to OPEN, which is the value the trader browse query in #62 filters
    on."""
    actor = _actor()
    market = await _submitted(session, actor)

    published = await market_service.publish(session, actor, market.id)

    assert published.status is MarketStatus.OPEN
    assert published.published_at is not None


async def test_publishing_survives_a_reload(session: AsyncSession) -> None:
    """Asserted from the database rather than the in-memory object, so this
    fails if the transition is never actually committed."""
    actor = _actor()
    market = await _submitted(session, actor)

    await market_service.publish(session, actor, market.id)

    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.OPEN


async def test_publishing_records_when_rather_than_reusing_submitted_at(
    session: AsyncSession,
) -> None:
    """Two decisions, two timestamps.

    A market can sit submitted for a week before anybody publishes it, and the
    gap is the thing worth being able to see.
    """
    actor = _actor()
    submitted_at = datetime.now(UTC)
    market = await _submitted(session, actor)
    assert market.submitted_at is not None

    later = submitted_at + timedelta(days=3)
    published = await market_service.publish(session, actor, market.id, now=later)

    assert published.published_at == later
    assert published.submitted_at != published.published_at


# --- which markets may be published --------------------------------------
async def test_a_draft_cannot_be_published(session: AsyncSession) -> None:
    """Publishing is a second decision, not a shortcut through the first.

    A draft has never been through the submission gate, and it is the one state
    where the three-second autosave is still overwriting the row.
    """
    actor = _actor()
    market, _, _ = await market_service.save(session, actor, _request())

    with pytest.raises(MarketNotSubmitted):
        await market_service.publish(session, actor, market.id)


async def test_a_draft_that_would_pass_every_rule_still_cannot_be_published(
    session: AsyncSession,
) -> None:
    """The state is what is refused, not the terms.

    `_request()` is complete enough to submit, so this draft would validate. It
    is still a 409, which is what keeps `submitted` from being a state an admin
    can skip.
    """
    actor = _actor()
    market, problems, _ = await market_service.save(session, actor, _request())
    assert problems == []

    with pytest.raises(MarketNotSubmitted):
        await market_service.publish(session, actor, market.id)


async def test_publishing_twice_is_refused(session: AsyncSession) -> None:
    """Rather than silently succeeding. A second entry in the audit log saying
    a market went live again would be noise, and the admin has more likely
    double-clicked than changed their mind."""
    actor = _actor()
    market = await _submitted(session, actor)
    await market_service.publish(session, actor, market.id)

    with pytest.raises(MarketAlreadyOpen):
        await market_service.publish(session, actor, market.id)


async def test_another_administrator_cannot_publish_it(session: AsyncSession) -> None:
    """404, not 403, and for the same reason reading it is: a 403 would confirm
    the market exists. Publication stays with the creator, like every other
    route in this service."""
    actor = _actor()
    market = await _submitted(session, actor)

    with pytest.raises(MarketNotFound):
        await market_service.publish(session, _actor(), market.id)


async def test_another_administrators_market_is_left_alone(
    session: AsyncSession,
) -> None:
    """The refusal above must not be a refusal that also wrote something."""
    actor = _actor()
    market = await _submitted(session, actor)

    with pytest.raises(MarketNotFound):
        await market_service.publish(session, _actor(), market.id)

    await session.rollback()
    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.SUBMITTED
    assert stored.published_at is None


async def test_publishing_a_market_that_does_not_exist_is_not_found(
    session: AsyncSession,
) -> None:
    with pytest.raises(MarketNotFound):
        await market_service.publish(session, _actor(), uuid.uuid4())


# --- the completeness gate ------------------------------------------------
async def test_publishing_is_blocked_when_the_terms_no_longer_pass(
    session: AsyncSession,
) -> None:
    """[1.3] #3's first criterion, and the case that makes it more than a
    formality.

    The market was complete when it was submitted. It is now sixty days later
    and its close time is in the past, so publishing it would put a market in
    front of traders that has already closed. The same pure function that
    guarded submission is re-run against the clock now.
    """
    actor = _actor()
    market = await _submitted(session, actor)

    with pytest.raises(DraftIncomplete) as raised:
        await market_service.publish(
            session, actor, market.id, now=datetime.now(UTC) + timedelta(days=60)
        )

    fields = {p.field for p in raised.value.problems}
    assert "close_time" in fields


async def test_a_blocked_publish_says_published_rather_than_submitted(
    session: AsyncSession,
) -> None:
    """Same error code and same `details` shape as a refused submission, so the
    form needs no second branch, but a sentence that describes what the admin
    actually pressed."""
    actor = _actor()
    market = await _submitted(session, actor)

    with pytest.raises(DraftIncomplete) as raised:
        await market_service.publish(
            session, actor, market.id, now=datetime.now(UTC) + timedelta(days=60)
        )

    assert raised.value.code == "draft_incomplete"
    assert "publish" in raised.value.message.lower()


async def test_a_blocked_publish_leaves_the_market_submitted(
    session: AsyncSession,
) -> None:
    """All-or-nothing, the same as a refused submission. A market half-way to
    open is a market nobody can reason about."""
    actor = _actor()
    market = await _submitted(session, actor)

    with pytest.raises(DraftIncomplete):
        await market_service.publish(
            session, actor, market.id, now=datetime.now(UTC) + timedelta(days=60)
        )

    await session.rollback()
    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.SUBMITTED
    assert stored.published_at is None


# --- publication freezes the market ---------------------------------------
async def test_a_late_autosave_cannot_touch_a_published_market(
    session: AsyncSession,
) -> None:
    """The form may still be open behind the publish button when the
    three-second timer next fires. Traders are pricing against these terms."""
    actor = _actor()
    key = uuid.uuid4()
    market = await _submitted(session, actor, draft_key=key)
    await market_service.publish(session, actor, market.id)

    with pytest.raises(MarketAlreadyOpen):
        await market_service.save(session, actor, _request(draft_key=key, question="Edited"))


async def test_a_resubmission_cannot_touch_a_published_market(
    session: AsyncSession,
) -> None:
    """The escape hatch that works on a submitted market is closed once it is
    open. Submitting again is how a submitted market is changed; there is no
    such route back from published."""
    actor = _actor()
    key = uuid.uuid4()
    market = await _submitted(session, actor, draft_key=key)
    await market_service.publish(session, actor, market.id)

    with pytest.raises(MarketAlreadyOpen):
        await market_service.save(
            session,
            actor,
            _request(draft_key=key, status="submitted", question="A different question?"),
        )


async def test_the_refusal_names_the_open_state_rather_than_the_submitted_one(
    session: AsyncSession,
) -> None:
    """MarketNotEditable offers a remedy — submit again — that does not exist
    for a published market, so the two cannot share an error."""
    actor = _actor()
    key = uuid.uuid4()
    market = await _submitted(session, actor, draft_key=key)
    # Read before the rollback below, which expires every loaded object and
    # would turn a later `market.id` into a lazy refresh outside the greenlet.
    market_id = market.id

    with pytest.raises(MarketNotEditable):
        await market_service.save(session, actor, _request(draft_key=key))

    await session.rollback()
    await market_service.publish(session, actor, market_id)

    with pytest.raises(MarketAlreadyOpen) as raised:
        await market_service.save(session, actor, _request(draft_key=key))

    assert raised.value.code == "market_already_open"


async def test_a_published_market_keeps_its_terms(session: AsyncSession) -> None:
    """The refusals above must actually protect the row, not merely report."""
    actor = _actor()
    key = uuid.uuid4()
    market = await _submitted(session, actor, draft_key=key, question="The published question?")
    await market_service.publish(session, actor, market.id)

    with pytest.raises(MarketAlreadyOpen):
        await market_service.save(
            session, actor, _request(draft_key=key, question="Something else entirely?")
        )

    await session.rollback()
    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.question == "The published question?"
    assert stored.status is MarketStatus.OPEN


# --- two requests at once -------------------------------------------------
async def test_two_concurrent_publishes_produce_one_winner(
    clean_database, audit_reader: AsyncSession
) -> None:
    """An administrator double-clicks the publish button.

    Two real transactions, not a simulated race. Read without a lock both would
    see SUBMITTED, both would flip the status, and the audit log would carry two
    entries each claiming to be the moment this market went live — which is
    precisely the thing [1.3] #3 promises the log can answer.

    `publish` takes a row lock, so under READ COMMITTED the loser blocks on the
    SELECT and then re-reads the row the winner committed. It finds OPEN and is
    refused.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    factory = get_session_factory()
    actor = _actor()

    async with factory() as setup:
        market = await _submitted(setup, actor)
        market_id = market.id

    async def attempt() -> str:
        async with factory() as own:
            try:
                await market_service.publish(own, actor, market_id)
            except MarketAlreadyOpen:
                return "refused"
            return "published"

    results = await asyncio.gather(attempt(), attempt())

    assert sorted(results) == ["published", "refused"]

    entries = [
        e for e in (await audit_reader.execute(
            _PUBLISHED_ENTRIES, {"actor": actor.id}
        )).mappings()
    ]
    assert len(entries) == 1, "the log must record one publication, not two"


async def test_a_resubmission_racing_a_publish_cannot_change_what_went_live(
    clean_database, audit_reader: AsyncSession
) -> None:
    """An administrator publishes while their own resubmission is in flight.

    `publish` holds the row, but a lock only queues other lockers, and the save
    path used to read without one. The resubmission saw SUBMITTED, passed the
    frozen check, waited on its own INSERTs for the publication to commit, and
    then landed anyway: new terms under a market traders were already pricing,
    and SUBMITTED written back over OPEN, while the publication entry in the
    log swore to the old terms.

    Either order is legitimate, so the assertions are about the market rather
    than about who won. If the publish wins, the resubmission is refused and
    the original terms are live; if the resubmission wins, the publish takes
    the row after it and the new terms are the ones that go live. What can
    never happen is a third thing: the market is OPEN, and the question
    traders see is the one the single publication entry recorded.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    factory = get_session_factory()
    actor = _actor()

    async with factory() as setup:
        market = await _submitted(setup, actor, question="The question as first submitted?")
        market_id, key = market.id, market.draft_key

    # Both connected before either starts, so the outcome is decided by the
    # lock and not by which session had to open a connection first.
    barrier = asyncio.Barrier(2)

    async def resubmit() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await market_service.save(
                    own,
                    actor,
                    _request(draft_key=key, status="submitted", question="The question as edited?"),
                )
            except MarketAlreadyOpen:
                return "refused"
            return "resubmitted"

    async def publish() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            await market_service.publish(own, actor, market_id)
            return "published"

    results = await asyncio.gather(resubmit(), publish())
    assert "published" in results

    async with factory() as check:
        stored = (
            await check.execute(select(Market).where(Market.id == market_id))
        ).scalar_one()

    published = list(
        (await audit_reader.execute(_PUBLISHED_ENTRIES, {"actor": actor.id})).mappings()
    )
    assert len(published) == 1, "the log must record one publication, not two"
    assert stored.status is MarketStatus.OPEN
    assert stored.question == published[0]["target_label"]


async def test_publish_reads_the_clock_after_it_has_the_lock(
    session: AsyncSession,
) -> None:
    """A publish that queued for the row lock validates against the clock it
    finds on the way out, not the one it arrived with. #97.

    The re-validation above is the ticket's first acceptance criterion: a
    market that sat submitted past its own close time must not go live. That
    argument assumes the clock read and the validation are the same instant.
    They were not — `now` was sampled at the top of the call, before a lock
    wait that is unbounded by construction, because anything else holding this
    row queues this request behind it.

    So the failure needs no clock skew and no slow database, only contention:
    the close time passes while the publish is blocked, the lock is released,
    and the check runs against a timestamp from before the wait. It answers
    "still in the future" about a market that has already closed, and the
    market goes live already closed — with a publication entry recording a
    `close_time` in the past.

    The lock here is held by a raw `SELECT ... FOR UPDATE` rather than by a
    second service call, because what is being tested is the wait itself. Any
    holder would do; this one is the shortest way to hold the row for a known
    length of time.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    factory = get_session_factory()
    actor = _actor()

    # Closes very soon, and the holder below outlasts it. Generous margins on
    # purpose: this asserts an ordering, and a tight one would fail on a busy
    # CI runner for reasons that have nothing to do with the ordering.
    async with factory() as setup:
        market = await _submitted(
            setup, actor, close_time=datetime.now(UTC) + timedelta(seconds=1.5)
        )
        market_id = market.id

    barrier = asyncio.Barrier(2)

    async def hold_the_row() -> None:
        async with factory() as own:
            await own.execute(select(Market).where(Market.id == market_id).with_for_update())
            # Locked before the barrier releases, so the publish below is
            # certain to queue rather than to win a coin toss.
            await barrier.wait()
            await asyncio.sleep(3)
            await own.rollback()

    async def publish_it() -> str:
        async with factory() as own:
            await own.connection()
            await barrier.wait()
            try:
                await market_service.publish(own, actor, market_id)
            except DraftIncomplete:
                return "refused"
            return "published"

    _, outcome = await asyncio.gather(hold_the_row(), publish_it())

    assert outcome == "refused", "a market whose close time passed while the publish waited"

    async with factory() as check:
        stored = (
            await check.execute(select(Market).where(Market.id == market_id))
        ).scalar_one()

    assert stored.status is MarketStatus.SUBMITTED
    assert stored.published_at is None
