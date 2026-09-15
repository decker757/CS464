"""Drafting and submitting, driven through the service layer without HTTP.

[1.1] #1's business rules live here: autosave is idempotent, a submission is
all-or-nothing, and a draft belongs to exactly one administrator.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.errors import DraftIncomplete, MarketNotEditable, MarketNotFound
from model.entities import Market, MarketStatus
from model.schemas import MarketDraftRequest
from service import market_service


def _request(**overrides: object) -> MarketDraftRequest:
    base: dict[str, object] = {
        "draft_key": uuid.uuid4(),
        "status": "draft",
        "question": "Will Singapore core inflation be below 2% in December 2026?",
        "outcomes": [{"label": "Yes"}, {"label": "No"}],
        "close_time": datetime.now(UTC) + timedelta(days=30),
        "resolution_time": datetime.now(UTC) + timedelta(days=45),
        "resolution_criteria": "Resolves YES on the first published MAS print below 2.0%.",
        "resolution_sources": [{"url": "https://www.mas.gov.sg/statistics"}],
        # No liquidity_b: omitting it is the common case, and it exercises the
        # configured default on every test that submits.
        "seed_subsidy": Decimal("250"),
    }
    base.update(overrides)
    return MarketDraftRequest(**base)  # type: ignore[arg-type]


async def _count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(Market))).scalar_one()


# --- autosave idempotency -------------------------------------------------
async def test_the_first_save_creates_one_market(session: AsyncSession) -> None:
    market, _, created = await market_service.save(session, uuid.uuid4(), _request())

    assert created is True
    assert market.status is MarketStatus.DRAFT
    assert await _count(session) == 1


async def test_repeated_autosaves_update_one_market(session: AsyncSession) -> None:
    """The whole reason draft_key exists.

    A three-second idle timer on an open form fires dozens of times. Without
    the key this would be dozens of abandoned drafts.
    """
    creator = uuid.uuid4()
    key = uuid.uuid4()

    for attempt in range(5):
        _, _, created = await market_service.save(
            session, creator, _request(draft_key=key, question=f"Draft revision {attempt}")
        )
        assert created is (attempt == 0)

    assert await _count(session) == 1
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.question == "Draft revision 4"


async def test_the_same_draft_key_from_two_creators_makes_two_markets(
    session: AsyncSession,
) -> None:
    """Uniqueness is per creator, so a client-side UUID collision cannot let
    one administrator write over another's market."""
    key = uuid.uuid4()

    await market_service.save(session, uuid.uuid4(), _request(draft_key=key))
    await market_service.save(session, uuid.uuid4(), _request(draft_key=key))

    assert await _count(session) == 2


async def test_a_draft_may_be_almost_empty(session: AsyncSession) -> None:
    """Autosave never refuses. The admin opened the form and typed one word."""
    market, problems, _ = await market_service.save(
        session,
        uuid.uuid4(),
        MarketDraftRequest(draft_key=uuid.uuid4(), question="Will"),
    )

    assert market.status is MarketStatus.DRAFT
    assert problems != []


async def test_a_draft_reports_what_would_block_submission(session: AsyncSession) -> None:
    """So the form can show the admin how far off they are, with no extra call
    and without duplicating the rules in the browser."""
    _, problems, _ = await market_service.save(
        session, uuid.uuid4(), _request(close_time=None, resolution_sources=[])
    )

    fields = {p.field for p in problems}
    assert "close_time" in fields
    assert "resolution_sources" in fields


async def test_clearing_a_field_stores_null_rather_than_an_empty_string(
    session: AsyncSession,
) -> None:
    creator, key = uuid.uuid4(), uuid.uuid4()
    await market_service.save(session, creator, _request(draft_key=key))

    market, _, _ = await market_service.save(
        session, creator, _request(draft_key=key, question="   ")
    )

    assert market.question is None


async def test_outcomes_are_replaced_wholesale_and_keep_their_order(
    session: AsyncSession,
) -> None:
    creator, key = uuid.uuid4(), uuid.uuid4()
    await market_service.save(session, creator, _request(draft_key=key))

    market, _, _ = await market_service.save(
        session,
        creator,
        _request(
            draft_key=key,
            outcomes=[{"label": "Below 1%"}, {"label": "1% to 2%"}, {"label": "Above 2%"}],
        ),
    )

    assert [o.label for o in market.outcomes] == ["Below 1%", "1% to 2%", "Above 2%"]
    assert [o.position for o in market.outcomes] == [0, 1, 2]


async def test_two_blank_outcomes_do_not_break_the_autosave(
    session: AsyncSession,
) -> None:
    """Why there is no unique index on lower(label).

    A half-typed form routinely holds two empty rows. A database constraint
    here would make autosave start failing exactly when the admin is
    mid-thought; label uniqueness is a submission rule instead.
    """
    market, _, _ = await market_service.save(
        session, uuid.uuid4(), _request(outcomes=[{"label": ""}, {"label": ""}])
    )

    assert len(market.outcomes) == 2


# --- submission -----------------------------------------------------------
async def test_a_complete_market_submits(session: AsyncSession) -> None:
    market, problems, _ = await market_service.save(
        session, uuid.uuid4(), _request(status="submitted")
    )

    assert market.status is MarketStatus.SUBMITTED
    assert market.submitted_at is not None
    assert problems == []


async def test_an_incomplete_market_is_refused_with_every_reason(
    session: AsyncSession,
) -> None:
    with pytest.raises(DraftIncomplete) as raised:
        await market_service.save(
            session,
            uuid.uuid4(),
            _request(status="submitted", question=None, resolution_criteria=None),
        )

    fields = {p.field for p in raised.value.problems}
    assert fields == {"question", "resolution_criteria"}


async def test_a_refused_submission_writes_nothing(session: AsyncSession) -> None:
    """All-or-nothing. A 422 that had also written half the change would be a
    worse contract than one that is simply atomic; the next autosave three
    seconds later is what keeps the edits from being lost."""
    with pytest.raises(DraftIncomplete):
        await market_service.save(
            session, uuid.uuid4(), _request(status="submitted", close_time=None)
        )

    await session.rollback()
    assert await _count(session) == 0


async def test_a_refused_submission_leaves_an_existing_draft_untouched(
    session: AsyncSession,
) -> None:
    creator, key = uuid.uuid4(), uuid.uuid4()
    await market_service.save(session, creator, _request(draft_key=key, question="Original"))

    with pytest.raises(DraftIncomplete):
        await market_service.save(
            session,
            creator,
            _request(draft_key=key, status="submitted", question="Edited", close_time=None),
        )

    await session.rollback()
    stored = (await session.execute(select(Market))).scalar_one()
    assert stored.question == "Original"
    assert stored.status is MarketStatus.DRAFT


async def test_a_market_can_be_submitted_after_being_drafted(
    session: AsyncSession,
) -> None:
    """The real sequence: the timer saves a few times, then the admin presses
    the button on the same form."""
    creator, key = uuid.uuid4(), uuid.uuid4()
    await market_service.save(session, creator, _request(draft_key=key, question="Half"))

    market, _, created = await market_service.save(
        session, creator, _request(draft_key=key, status="submitted")
    )

    assert created is False
    assert market.status is MarketStatus.SUBMITTED
    assert await _count(session) == 1


async def test_a_late_autosave_cannot_revert_a_submitted_market(
    session: AsyncSession,
) -> None:
    """The form is still open after the submit button, and the timer fires
    again. Letting that through would silently un-submit a finished market."""
    creator, key = uuid.uuid4(), uuid.uuid4()
    await market_service.save(session, creator, _request(draft_key=key, status="submitted"))

    with pytest.raises(MarketNotEditable):
        await market_service.save(session, creator, _request(draft_key=key, status="draft"))


async def test_a_submitted_market_can_be_submitted_again(session: AsyncSession) -> None:
    """A deliberate edit is allowed; it just has to pass every rule again."""
    creator, key = uuid.uuid4(), uuid.uuid4()
    await market_service.save(session, creator, _request(draft_key=key, status="submitted"))

    market, _, _ = await market_service.save(
        session,
        creator,
        _request(draft_key=key, status="submitted", question="A corrected question here?"),
    )

    assert market.question == "A corrected question here?"
    assert market.status is MarketStatus.SUBMITTED


async def test_submission_is_judged_against_the_clock_it_is_given(
    session: AsyncSession,
) -> None:
    """A market whose close time has passed cannot be submitted, even though
    the same draft was valid when it was written."""
    request = _request(status="submitted")

    with pytest.raises(DraftIncomplete):
        await market_service.save(
            session, uuid.uuid4(), request, now=datetime.now(UTC) + timedelta(days=60)
        )


async def test_losing_an_insert_race_still_saves(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two autosaves for one form in flight at once, when the first is slow.

    Both look for an existing row and both see none, so the second insert
    violates the unique constraint. The loser retries against the row the
    winner created rather than 500ing on a request that is entirely valid.

    The race is forced rather than raced: the lookup is blinded exactly once,
    which puts this pass in the loser's position deterministically instead of
    depending on scheduling.
    """
    creator, key = uuid.uuid4(), uuid.uuid4()
    await market_service.save(session, creator, _request(draft_key=key, question="Winner"))

    real_lookup = market_service._find_by_draft_key
    calls = {"n": 0}

    async def blind_once(*args: object, **kwargs: object):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_lookup(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(market_service, "_find_by_draft_key", blind_once)

    market, _, created = await market_service.save(
        session, creator, _request(draft_key=key, question="Loser arrives second")
    )

    assert calls["n"] == 2, "the retry must go back to the database"
    assert created is False
    assert market.question == "Loser arrives second"
    assert await _count(session) == 1


# --- visibility -----------------------------------------------------------
async def test_a_creator_can_read_their_own_market(session: AsyncSession) -> None:
    creator = uuid.uuid4()
    market, _, _ = await market_service.save(session, creator, _request())

    assert (await market_service.get(session, creator, market.id)).id == market.id


async def test_another_administrator_cannot_read_it(session: AsyncSession) -> None:
    """[1.1] #1: a draft is visible to its creator and to nobody else,
    including other administrators."""
    market, _, _ = await market_service.save(session, uuid.uuid4(), _request())

    with pytest.raises(MarketNotFound):
        await market_service.get(session, uuid.uuid4(), market.id)


async def test_a_missing_market_and_someone_elses_are_indistinguishable(
    session: AsyncSession,
) -> None:
    """Same error for both, so the response cannot be used to discover that a
    market exists."""
    market, _, _ = await market_service.save(session, uuid.uuid4(), _request())
    intruder = uuid.uuid4()

    with pytest.raises(MarketNotFound) as theirs:
        await market_service.get(session, intruder, market.id)
    with pytest.raises(MarketNotFound) as absent:
        await market_service.get(session, intruder, uuid.uuid4())

    assert theirs.value.code == absent.value.code
    assert theirs.value.message == absent.value.message


async def test_the_list_holds_only_the_callers_markets(session: AsyncSession) -> None:
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    await market_service.save(session, mine, _request())
    await market_service.save(session, mine, _request())
    await market_service.save(session, theirs, _request())

    assert len(await market_service.list_for_creator(session, mine)) == 2
    assert len(await market_service.list_for_creator(session, theirs)) == 1


async def test_the_list_is_empty_for_an_administrator_with_no_markets(
    session: AsyncSession,
) -> None:
    assert await market_service.list_for_creator(session, uuid.uuid4()) == []


# --- pricing [1.2] #2 -----------------------------------------------------
async def test_an_omitted_liquidity_takes_the_configured_default(
    session: AsyncSession,
) -> None:
    """"b defaults to a configured value", so a market is priceable from the
    first save and the form can show a worst case immediately."""
    market, _, _ = await market_service.save(session, uuid.uuid4(), _request())

    assert market.liquidity_b == get_settings().default_liquidity_b


async def test_an_explicit_liquidity_overrides_the_default(
    session: AsyncSession,
) -> None:
    market, _, _ = await market_service.save(
        session, uuid.uuid4(), _request(liquidity_b=Decimal("250"))
    )

    assert market.liquidity_b == Decimal("250")


async def test_the_liquidity_can_be_changed_by_a_later_autosave(
    session: AsyncSession,
) -> None:
    """The admin raises b, sees the larger worst case, and puts it back.

    Last-write-wins on the whole document, the same as every other term, so
    reverting has to work as well as setting.
    """
    creator, key = uuid.uuid4(), uuid.uuid4()

    await market_service.save(
        session, creator, _request(draft_key=key, liquidity_b=Decimal("500"))
    )
    market, _, _ = await market_service.save(
        session, creator, _request(draft_key=key, liquidity_b=Decimal("100"))
    )

    assert market.liquidity_b == Decimal("100")


async def test_an_omitted_subsidy_stays_null_rather_than_taking_a_default(
    session: AsyncSession,
) -> None:
    """There is no sensible platform-wide answer to how much this particular
    market is worth underwriting, so absence is left for the admin to fill."""
    market, problems, _ = await market_service.save(
        session, uuid.uuid4(), _request(seed_subsidy=None)
    )

    assert market.seed_subsidy is None
    assert "seed_subsidy" in {p.field for p in problems}


async def test_a_market_seeded_below_its_worst_case_still_submits(
    session: AsyncSession,
) -> None:
    """b = 100 over two outcomes risks about 69.31; this seeds 1.

    Deliberate, and recorded in service/validation._liquidity_problems: the
    worst case is reported on every save, so underfunding is a choice the admin
    makes with the number in front of them.
    """
    market, problems, _ = await market_service.save(
        session,
        uuid.uuid4(),
        _request(status="submitted", liquidity_b=Decimal("100"), seed_subsidy=Decimal("1")),
    )

    assert market.status is MarketStatus.SUBMITTED
    assert problems == []
