"""Publishing a market, driven through the service layer without HTTP. [1.3] #3.

The transition and its rules. HTTP answers are in unit_test/controller, audit
entries in test_audit.py.
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

# Aliased so the names do not shadow an `actor` argument.
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
    """[1.3] #3's second criterion, this service's half: the status moves to OPEN."""
    actor = _actor()
    market = await _submitted(session, actor)

    published = await market_service.publish(session, actor, market.id)

    assert published.status is MarketStatus.OPEN
    assert published.published_at is not None


async def test_publishing_survives_a_reload(session: AsyncSession) -> None:
    """Read from the database, so this fails if the transition is never committed."""
    actor = _actor()
    market = await _submitted(session, actor)

    await market_service.publish(session, actor, market.id)

    session.expire_all()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.status is MarketStatus.OPEN


async def test_publishing_records_when_rather_than_reusing_submitted_at(
    session: AsyncSession,
) -> None:
    """Two decisions, two timestamps: the gap between them is worth seeing."""
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
    """Publishing is a second decision, not a shortcut through the first. ADR 0008."""
    actor = _actor()
    market, _, _ = await market_service.save(session, actor, _request())

    with pytest.raises(MarketNotSubmitted):
        await market_service.publish(session, actor, market.id)


async def test_a_draft_that_would_pass_every_rule_still_cannot_be_published(
    session: AsyncSession,
) -> None:
    """The state is refused, not the terms, so `submitted` cannot be skipped."""
    actor = _actor()
    market, problems, _ = await market_service.save(session, actor, _request())
    assert problems == []

    with pytest.raises(MarketNotSubmitted):
        await market_service.publish(session, actor, market.id)


async def test_publishing_twice_is_refused(session: AsyncSession) -> None:
    """Refused rather than silently succeeding. ADR 0008."""
    actor = _actor()
    market = await _submitted(session, actor)
    await market_service.publish(session, actor, market.id)

    with pytest.raises(MarketAlreadyOpen):
        await market_service.publish(session, actor, market.id)


async def test_another_administrator_cannot_publish_it(session: AsyncSession) -> None:
    """404, not 403: publication stays with the creator. ADR 0008."""
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
    """[1.3] #3's first criterion: sixty days on, the close time has passed. ADR 0008."""
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
    """Same code and `details` as a refused submission; only the sentence differs. ADR 0008."""
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
    """All-or-nothing, the same as a refused submission."""
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
    """The form behind the publish button autosaves again. ADR 0008."""
    actor = _actor()
    key = uuid.uuid4()
    market = await _submitted(session, actor, draft_key=key)
    await market_service.publish(session, actor, market.id)

    with pytest.raises(MarketAlreadyOpen):
        await market_service.save(session, actor, _request(draft_key=key, question="Edited"))


async def test_a_resubmission_cannot_touch_a_published_market(
    session: AsyncSession,
) -> None:
    """Submitting again changes a submitted market, never a published one."""
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
    """MarketNotEditable's remedy, submit again, does not exist once published."""
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
    """A double-clicked publish: two real transactions, one publication in the log.

    The loser blocks on the row lock, re-reads OPEN and is refused. ADR 0015.
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
    """A resubmission racing a publish must not write SUBMITTED back over OPEN. ADR 0015.

    Either may win, so the assertions are about the result: the market is
    OPEN, and its question is the one the single publication entry recorded.
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
    """A publish that queued for the row lock validates against the clock after
    the wait, so a close time that passed meanwhile blocks it. #97.

    A raw `SELECT ... FOR UPDATE` holds the row, because the wait is the subject.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_session_factory  # noqa: PLC0415

    factory = get_session_factory()
    actor = _actor()

    # Closes soon, and the holder below outlasts it. Margins are generous so a
    # busy CI runner cannot fail the ordering.
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
