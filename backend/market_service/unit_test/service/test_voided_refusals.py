"""Every write path refuses a voided market. [BE] #221, ADR 0025.

"Every later write is `409 market_already_voided`: save, submit, publish,
close early, propose, approve, reject, send-back and settle." The repeated
void, the one exception, arrives with the route. That a refused autosave
leaves the market intact is the controller suite's, because it must be read
back from a second session after the request has finished.
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
    send_back_request,
    voided_market,
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
    return await market_service.close_early(
        session, actor(), market.id, close_request()
    )


async def _propose(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.propose_outcome(
        session, creator, market.id, proposal_request(market.outcomes[0].id)
    )


# A decision that arrives after the void, quoting the proposal it read.
async def _approve(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.approve_outcome(
        session, actor(), market.id, approval_request(market.proposal_id)
    )


async def _reject(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.reject_outcome(
        session, actor(), market.id, rejection_request(market.proposal_id)
    )


async def _send_back(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.send_back_outcome(
        session, actor(), market.id, send_back_request(market.proposal_id)
    )


async def _settle(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.settle(session, actor(), market.id)


# The proposer is the creator. State is checked before identity, so the
# proposer hears `market_already_voided`, not `second_administrator_required`.
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
        _send_back,
        _settle,
        _approve_as_proposer,
        _reject_as_proposer,
    ],
    ids=lambda write: write.__name__.lstrip("_"),
)
async def test_each_write_on_a_voided_market_is_409_market_already_voided(
    session: AsyncSession, write: _Write
) -> None:
    """ADR 0025: "Every later write is `409 market_already_voided`".

    Each case fails with VOIDED removed from `_PAST_DECISION_STATUS_ERRORS`,
    which the frozen and resolution tables spread, except two: send-back and
    settle have state checks of their own, and each fails with its own
    VOIDED branch removed. The proposer cases fail if identity is checked
    before state.
    """
    creator = actor()
    market = await voided_market(session, creator)

    with pytest.raises(MarketError) as refused:
        await write(session, creator, market)

    assert refused.value.code == "market_already_voided"
    assert refused.value.status_code == 409
