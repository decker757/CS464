"""Every write path refuses a settled market. [3.4] #12, PR 1.

"A settled market's terms can no longer be saved, submitted, published,
closed early, proposed for, approved or rejected; each is refused with
`409 market_already_settled`." "A settled market is refused after the locked
read and before any write, and the status tables stay separate". That a
refused save leaves the market intact is the controller suite's, because it
must be read back from a second session after the request has finished.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketError
from model.entities import Market
from service import market_service
from service.audit import Actor
from unit_test.conftest import (
    actor,
    approval_request,
    close_request,
    draft_request,
    proposal_request,
    rejection_request,
    settled_market,
)

_Write = Callable[[AsyncSession, Actor, Market], Awaitable[object]]


async def _save_draft(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.save(
        session, creator, draft_request(draft_key=market.draft_key, status="draft")
    )


async def _save_submit(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.save(
        session, creator, draft_request(draft_key=market.draft_key, status="submitted")
    )


async def _publish(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.publish(session, creator, market.id)


async def _close_early(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.close_early(session, actor(), market.id, close_request())


async def _propose(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.propose_outcome(
        session, creator, market.id, proposal_request(market.outcomes[0].id)
    )


async def _approve(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.approve_outcome(
        session, actor(), market.id, approval_request(market.proposal_id)
    )


async def _reject(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.reject_outcome(
        session, actor(), market.id, rejection_request(market.proposal_id)
    )


# The proposer is the creator. State is checked before identity, so the
# proposer hears `market_already_settled`, not `second_administrator_required`.
async def _approve_as_proposer(
    session: AsyncSession, creator: Actor, market: Market
) -> object:
    return await market_service.approve_outcome(
        session, creator, market.id, approval_request(market.proposal_id)
    )


async def _reject_as_proposer(
    session: AsyncSession, creator: Actor, market: Market
) -> object:
    return await market_service.reject_outcome(
        session, creator, market.id, rejection_request(market.proposal_id)
    )


@pytest.mark.parametrize(
    "write",
    [
        _save_draft,
        _save_submit,
        _publish,
        _close_early,
        _propose,
        _approve,
        _reject,
        _approve_as_proposer,
        _reject_as_proposer,
    ],
    ids=lambda write: write.__name__.lstrip("_"),
)
async def test_each_write_on_a_settled_market_is_409_market_already_settled(
    session: AsyncSession, write: _Write
) -> None:
    """[3.4] #12: each refused "with `409 market_already_settled`".

    Each case fails with SETTLED removed from its own table: the frozen one
    for save and publish, the resolution one for close and propose,
    `_proposal_to_decide`'s state check for the decisions. The proposer cases
    fail if identity is checked before state.
    """
    creator = actor()
    market = await settled_market(session, creator)

    with pytest.raises(MarketError) as refused:
        await write(session, creator, market)

    assert refused.value.code == "market_already_settled"
    assert refused.value.status_code == 409
