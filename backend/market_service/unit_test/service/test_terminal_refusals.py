"""Every write path refuses a settled or a voided market. [3.4] #12, [BE] #221.

[3.4] #12: "A settled market's terms can no longer be saved, submitted,
published, closed early, proposed for, approved or rejected; each is refused
with `409 market_already_settled`." ADR 0025: on a voided market "Every later
write is `409 market_already_voided`: save, submit, publish, close early,
propose, approve, reject, send-back and settle." The repeated void, the one
exception, arrives with the route. That a refused autosave leaves the market
intact is the controller suite's, because it must be read back from a second
session after the request has finished.
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
    settled_market,
    voided_market,
)

_Build = Callable[[AsyncSession, Actor], Awaitable[Market]]
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


# A decision that arrives too late, quoting the proposal it read.
async def _approve(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.approve_outcome(
        session, actor(), market.id, approval_request(market.proposal_id)
    )


async def _reject(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.reject_outcome(
        session, actor(), market.id, rejection_request(market.proposal_id)
    )


# [3.3] #11. A settled market's window has long ended, so this also proves
# send-back checks the state before the window.
async def _send_back(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.send_back_outcome(
        session, actor(), market.id, send_back_request(market.proposal_id)
    )


# Voided only: settling a settled market answers 200, unwritten and unlogged.
async def _settle(session: AsyncSession, creator: Actor, market: Market) -> object:
    return await market_service.settle(session, actor(), market.id)


# The proposer is the creator. State is checked before identity, so the
# proposer hears the state's refusal, not `second_administrator_required`.
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


_WRITES_REFUSED_BY_BOTH: list[_Write] = [
    _save_draft,
    _save_submit,
    _publish,
    _close_early,
    _propose,
    _approve,
    _reject,
    _send_back,
    _approve_as_proposer,
    _reject_as_proposer,
]


def _cases(build: _Build, code: str, writes: list[_Write]) -> list[object]:
    """One case per write on a market from `build`, each expecting `code`."""
    cases = []
    for write in writes:
        name = f"{build.__name__}-{write.__name__.lstrip('_')}"
        cases.append(pytest.param(build, code, write, id=name))
    return cases


@pytest.mark.parametrize(
    ("build", "expected_code", "write"),
    _cases(settled_market, "market_already_settled", _WRITES_REFUSED_BY_BOTH)
    + _cases(
        voided_market, "market_already_voided", [*_WRITES_REFUSED_BY_BOTH, _settle]
    ),
)
async def test_each_write_on_a_settled_or_voided_market_is_a_409_naming_that_status(
    session: AsyncSession, build: _Build, expected_code: str, write: _Write
) -> None:
    """[3.4] #12: each refused "with `409 market_already_settled`"; ADR 0025:
    "Every later write is `409 market_already_voided`".

    A settled case fails with SETTLED removed from its own table: the frozen
    one for save and publish, the resolution one for close and propose,
    `_proposal_to_decide`'s state check for the decisions, send-back's own
    state check for send-back ([3.3] #11). A voided case fails with VOIDED
    removed from `_PAST_DECISION_STATUS_ERRORS`, which the frozen and
    resolution tables spread, except send-back and settle, which each fail
    with their own VOIDED branch removed. The proposer cases fail if
    identity is checked before state.
    """
    creator = actor()
    market = await build(session, creator)

    with pytest.raises(MarketError) as refused:
        await write(session, creator, market)

    assert refused.value.code == expected_code
    assert refused.value.status_code == 409
