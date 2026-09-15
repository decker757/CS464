"""Business rules for drafting and submitting a market. [1.1] #1.

HTTP is not mentioned in this file; failures are raised as domain errors. Every
function takes the caller's id as an argument rather than reading it from
anywhere ambient, which is what makes "only the creator can see this" a rule
that can be tested without a request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.errors import (
    DraftIncomplete,
    MarketNotEditable,
    MarketNotFound,
    ValidationProblem,
)
from model.audit import AdminAction
from model.entities import Market, MarketOutcome, MarketStatus, ResolutionSource
from model.schemas import PRICING_DECIMAL_PLACES, MarketDraftRequest
from service import audit
from service.audit import Actor
from service.validation import problems_blocking_submission


async def save(
    session: AsyncSession,
    actor: Actor,
    data: MarketDraftRequest,
    *,
    now: datetime | None = None,
) -> tuple[Market, list[ValidationProblem], bool]:
    """Create or update the caller's market identified by `data.draft_key`.

    Takes the whole `Actor` rather than a bare creator id because a submission
    writes an audit entry, and that entry has to name who the administrator was
    at the time. This service cannot look a user id up in `auth.users`, so the
    username and role travel with the request or not at all. [4.3] #15.

    Returns the market, the problems that would block submission, and whether
    this call created it, which the controller turns into 201 versus 200.

    The three-second autosave calls this with status DRAFT, possibly hundreds
    of times for one market. That is why the lookup is by (creator_id,
    draft_key) rather than an insert: the browser has no id to send until the
    first response arrives, and it may never arrive.

    A submission that fails validation writes nothing at all. The edits that
    came with it are not lost, because the next autosave three seconds later
    saves them as a draft; a 422 that had also written half the change would be
    a worse contract than one that is simply atomic.
    """
    now = now or datetime.now(UTC)

    try:
        return await _save_once(session, actor, data, now)
    except IntegrityError:
        # Two autosaves for the same form were in flight at once — a slow
        # request still running when the timer fired again — and both saw no
        # existing row. The unique constraint on (creator_id, draft_key) is
        # what catches the loser.
        #
        # Retried rather than surfaced. The loser's payload is the newer one,
        # and the row the winner just created is exactly the row it wanted to
        # write to, so the second pass finds it and updates it. Letting the
        # IntegrityError out would 500 on a request that is entirely valid.
        await session.rollback()
        return await _save_once(session, actor, data, now)


async def _save_once(
    session: AsyncSession,
    actor: Actor,
    data: MarketDraftRequest,
    now: datetime,
) -> tuple[Market, list[ValidationProblem], bool]:
    """One attempt. Raises IntegrityError if it lost an insert race."""
    market = await _find_by_draft_key(session, actor.id, data.draft_key)
    created = market is None

    if market is None:
        market = Market(creator_id=actor.id, draft_key=data.draft_key)
        session.add(market)
    else:
        if market.status is MarketStatus.SUBMITTED and data.status is MarketStatus.DRAFT:
            # An autosave arriving after the submit button was pressed. Letting
            # it through would silently revert a finished market to a draft,
            # and the admin would have no reason to suspect it. Changing a
            # submitted market means submitting it again, which re-runs every
            # rule.
            raise MarketNotEditable

        await _clear_children(session, market)

    # Read at the edge rather than inside `_apply`, so the rule that a
    # market falls back to the configured liquidity stays a pure function
    # of its arguments and can be tested without touching the environment.
    _apply(market, data, get_settings().default_liquidity_b)

    problems = problems_blocking_submission(market, now=now)
    submitting = data.status is MarketStatus.SUBMITTED

    if submitting:
        if problems:
            # Nothing is committed. The caller's session dependency rolls the
            # transaction back as this propagates, so the edits that arrived
            # with the rejected submission are discarded too — and so does the
            # audit entry below, which is never reached anyway.
            raise DraftIncomplete(problems)
        market.status = MarketStatus.SUBMITTED
        market.submitted_at = now
    else:
        market.status = MarketStatus.DRAFT

    await session.flush()

    if submitting:
        # After the flush, so a market created by this very call already has
        # the id the entry names. Before the commit, so the entry and the
        # submission are one atomic write: if the commit fails, there is no
        # orphan record of a submission that never happened, and if it
        # succeeds there is no submission with no record of who made it.
        # [4.3] #15.
        await _record_submission(session, actor, market, now)

    await session.commit()
    return market, problems, created


async def get(session: AsyncSession, creator_id: uuid.UUID, market_id: uuid.UUID) -> Market:
    """One of the caller's own markets.

    `creator_id` is part of the query rather than a check after the fact. A
    filter cannot be forgotten the way an `if` can, and an unfiltered read
    followed by a permission check is how a draft leaks.
    """
    stmt = select(Market).where(Market.id == market_id, Market.creator_id == creator_id)
    market = (await session.execute(stmt)).scalar_one_or_none()
    if market is None:
        raise MarketNotFound
    return market


async def list_for_creator(session: AsyncSession, creator_id: uuid.UUID) -> list[Market]:
    """Every market the caller owns, most recently touched first."""
    stmt = (
        select(Market)
        .where(Market.creator_id == creator_id)
        .order_by(Market.updated_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def _record_submission(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append the audit entry for a market leaving DRAFT. [4.3] #15.

    Submission is logged; autosave is not. An autosave fires every three
    seconds while the form is open, so recording it would bury every real
    decision under thousands of keystroke entries. Submission is the moment the
    administrator committed to a set of terms, and it is the moment worth being
    able to point at later.

    No `reason` is recorded, because the form does not ask for one and an empty
    string would be worse than a null. The stories that do demand a reason —
    [2.3] #7 closing a market early, [4.2] #14 suspending an account — pass one
    through the same argument.

    The terms go into `context` rather than being left to a later read of
    `market.markets`, because [1.4] #4 will allow a submitted market to be
    edited by submitting it again. Without the snapshot, the log would say that
    a market was submitted and the table would only ever show its latest terms,
    so the one question the log exists to answer — what did they actually
    approve — would need a row that no longer exists.
    """
    await audit.record(
        session,
        actor=actor,
        action=AdminAction.MARKET_SUBMITTED,
        target_type="market",
        target_id=market.id,
        target_label=market.question,
        context={
            # Stringified rather than floated. Nothing reads these to do
            # arithmetic with, unlike MarketOut's wire format, and the subsidy
            # is money: a record of what was approved should say 250.0000 and
            # not something that once rounded to it.
            "liquidity_b": _decimal_or_none(market.liquidity_b),
            "seed_subsidy": _decimal_or_none(market.seed_subsidy),
            "outcomes": [outcome.label for outcome in market.outcomes],
            "close_time": market.close_time.isoformat() if market.close_time else None,
            "resolution_time": (
                market.resolution_time.isoformat() if market.resolution_time else None
            ),
            "resolution_sources": [
                source.url for source in market.resolution_sources
            ],
        },
        now=now,
    )


# The scale the pricing columns hold, borrowed from the request schema so one
# change to Numeric(18, 4) moves both.
_PRICING_QUANTUM = Decimal(1).scaleb(-PRICING_DECIMAL_PLACES)


def _decimal_or_none(value: Decimal | None) -> str | None:
    """Stringify at the column's scale, so two entries for one value match.

    A market created by this very call still holds the Decimal the request
    carried, while one loaded from Postgres holds the value as Numeric(18, 4)
    returned it. Left alone, the same 250 credits would be logged as "250" on
    the first submission and "250.0000" on the next, and a reader comparing two
    entries would have to work out whether anything had actually changed.

    Never rounds: model/schemas.py already refuses anything with more decimal
    places than the column can hold, so this only ever pads.
    """
    return str(value.quantize(_PRICING_QUANTUM)) if value is not None else None


async def _find_by_draft_key(
    session: AsyncSession, creator_id: uuid.UUID, draft_key: uuid.UUID
) -> Market | None:
    stmt = select(Market).where(
        Market.creator_id == creator_id, Market.draft_key == draft_key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _clear_children(session: AsyncSession, market: Market) -> None:
    """Delete the existing outcomes and sources, and flush that on its own.

    Load-bearing rather than tidiness. Within a single flush SQLAlchemy issues
    the INSERTs for the replacement rows before the DELETEs for the orphans, so
    the new position 0 meets the old position 0 still sitting in the table and
    uq_outcome_position fires: every autosave after the first would 500.

    Only ever called for a market that was loaded from the database, so both
    collections are already populated by the selectin loader and nothing here
    goes back for them. A newly created market has no rows to collide with and
    skips this entirely, which also keeps the assignment in `_apply` off a
    freshly flushed object whose collections would have to be loaded to be
    replaced.
    """
    market.outcomes.clear()
    market.resolution_sources.clear()
    await session.flush()


def _apply(
    market: Market, data: MarketDraftRequest, default_liquidity_b: Decimal
) -> None:
    """Overwrite the market's terms with what the form last held.

    Last-write-wins on the whole document, because that is what an autosave of
    a single open form actually is. It is also the limitation: two browser tabs
    editing one draft_key will overwrite each other with no warning, which is
    recorded in ADR 0004 rather than solved, since a draft has one author by
    definition.
    """
    market.question = _blank_to_none(data.question)
    market.description = _blank_to_none(data.description)
    market.close_time = data.close_time
    market.resolution_time = data.resolution_time
    market.resolution_criteria = _blank_to_none(data.resolution_criteria)

    # [1.2] #2. An omitted `b` takes the configured default rather than staying
    # null, so a market is always priceable and the form can show a max loss
    # from the very first save. An explicit value overrides it, including on a
    # later save: the admin can raise `b`, see the larger worst case, and put
    # it back. The subsidy has no default — there is no sensible platform-wide
    # answer to how much this particular market is worth underwriting.
    market.liquidity_b = (
        data.liquidity_b if data.liquidity_b is not None else default_liquidity_b
    )
    market.seed_subsidy = data.seed_subsidy

    # Replaced wholesale rather than diffed; `_clear_children` has already
    # emptied and flushed whatever was there.
    #
    # Position comes from the order the client sent, so the arrangement the
    # admin made survives a reload, and [1.2] #2 and epic 3 have a stable
    # handle on an outcome that does not depend on its editable label.
    market.outcomes = [
        MarketOutcome(position=index, label=(item.label or "").strip())
        for index, item in enumerate(data.outcomes)
    ]
    market.resolution_sources = [
        ResolutionSource(
            position=index,
            url=(item.url or "").strip(),
            label=_blank_to_none(item.label),
        )
        for index, item in enumerate(data.resolution_sources)
    ]


def _blank_to_none(value: str | None) -> str | None:
    """A cleared form field should read as absent, not as an empty string.

    Otherwise "" and null both mean "not filled in" and every reader has to
    handle both.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
