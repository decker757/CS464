"""Business rules for drafting and submitting a market. [1.1] #1.

HTTP is not mentioned in this file; failures are raised as domain errors. Every
function takes the caller's id as an argument rather than reading it from
anywhere ambient, which is what makes "only the creator can see this" a rule
that can be tested without a request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    DraftIncomplete,
    MarketNotEditable,
    MarketNotFound,
    ValidationProblem,
)
from model.entities import Market, MarketOutcome, MarketStatus, ResolutionSource
from model.schemas import MarketDraftRequest
from service.validation import problems_blocking_submission


async def save(
    session: AsyncSession,
    creator_id: uuid.UUID,
    data: MarketDraftRequest,
    *,
    now: datetime | None = None,
) -> tuple[Market, list[ValidationProblem], bool]:
    """Create or update the caller's market identified by `data.draft_key`.

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
        return await _save_once(session, creator_id, data, now)
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
        return await _save_once(session, creator_id, data, now)


async def _save_once(
    session: AsyncSession,
    creator_id: uuid.UUID,
    data: MarketDraftRequest,
    now: datetime,
) -> tuple[Market, list[ValidationProblem], bool]:
    """One attempt. Raises IntegrityError if it lost an insert race."""
    market = await _find_by_draft_key(session, creator_id, data.draft_key)
    created = market is None

    if market is None:
        market = Market(creator_id=creator_id, draft_key=data.draft_key)
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

    _apply(market, data)

    problems = problems_blocking_submission(market, now=now)

    if data.status is MarketStatus.SUBMITTED:
        if problems:
            # Nothing is committed. The caller's session dependency rolls the
            # transaction back as this propagates, so the edits that arrived
            # with the rejected submission are discarded too.
            raise DraftIncomplete(problems)
        market.status = MarketStatus.SUBMITTED
        market.submitted_at = now
    else:
        market.status = MarketStatus.DRAFT

    await session.flush()
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


def _apply(market: Market, data: MarketDraftRequest) -> None:
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
