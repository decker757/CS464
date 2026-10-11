"""The portfolio after settlement. [3.4] #12

DECISIONS.md: "A settled position shows `result` and `payout`, and its value
fields are null", "A settled row's payout is its quantity, and its result comes
from `ledger.market_results`, joined in the portfolio's one statement", and
"`result` and `payout` are on every portfolio row, null until the market is
settled". The wire shape is `controller/test_portfolio_routes.py`'s.

Settled markets come from `settlement_fixtures.py`: positions inserted on a
warm book, then the real `pay_out`. The winner is outcome position 1, never
0, so a read that takes a market's first outcome as its winner fails here.
Open markets come from a real buy, valued at `HOLDING_VALUE`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.portfolio_fixtures import (
    HOLDING_VALUE,
    entries_sum,
    funded,
    market_at,
    read_portfolio,
    row,
)
from unit_test.sell_fixtures import hold
from unit_test.settlement_fixtures import (
    LOSER,
    PAID,
    UNPAID,
    WINNER,
    SettleUpstream,
    account_id_of,
    legs_of,
    payout_key,
    settle,
    settled_market,
    transaction_by_key,
    warm,
)
from unit_test.settlement_fixtures import hold as hold_shares
from unit_test.trade_fixtures import ZERO, entities, set_q


async def _paid_to(
    session: AsyncSession, market_id: uuid.UUID, user_id: uuid.UUID
) -> Decimal:
    """The amount this user's own leg of their payout transaction carried."""
    payout = await transaction_by_key(session, payout_key(market_id, user_id))
    assert payout is not None, "the user was never paid"
    legs = await legs_of(session, payout.id)
    return legs[await account_id_of(session, entities().AccountKind.USER, user_id)]


async def _one_settled_one_open(session: AsyncSession):
    """A funded user holding #23's 54.3333 in an open market and `PAID` of a
    market's winner. Returns the user, both markets and the portfolio read
    just before the settlement."""
    user_id, _ = await funded(session)
    open_market = await market_at(session)
    await hold(session, open_market, user_id=user_id)

    upstream = SettleUpstream()
    await warm(session, upstream)
    await hold_shares(session, upstream, [(user_id, WINNER, PAID)])
    before = await read_portfolio(session, user_id)
    await settle(session, upstream)
    return user_id, open_market, upstream, before


# =========================================================================
# The settled rows
# =========================================================================
async def test_a_winning_holder_s_row_is_paid_out_at_its_quantity_and_keeps_its_cost(
    session: AsyncSession,
) -> None:
    """The payout equals the quantity and the entry that paid it, and the
    holding's own figures are the ones it had before settlement."""
    user_id, _ = await funded(session)
    upstream = SettleUpstream()
    await warm(session, upstream)
    await hold_shares(
        session, upstream, [(user_id, WINNER, PAID), (uuid.uuid4(), LOSER, UNPAID)]
    )
    before = row(await read_portfolio(session, user_id), upstream.market_id, WINNER)

    await settle(session, upstream)
    after = row(await read_portfolio(session, user_id), upstream.market_id, WINNER)

    assert after.outcome_id == upstream.outcomes[1]
    assert after.result == "paid_out"
    assert after.payout == after.quantity == PAID
    assert after.payout == await _paid_to(session, upstream.market_id, user_id)
    assert (after.quantity, after.cost_basis, after.average_entry_price) == (
        before.quantity,
        before.cost_basis,
        before.average_entry_price,
    )


async def test_a_losing_holder_s_row_is_worthless_with_a_payout_of_zero_at_scale_four(
    session: AsyncSession,
) -> None:
    user_id, _ = await funded(session)
    upstream = await settled_market(
        session, [(user_id, LOSER, UNPAID), (uuid.uuid4(), WINNER, PAID)]
    )

    loser = row(await read_portfolio(session, user_id), upstream.market_id, LOSER)

    assert loser.result == "worthless"
    assert loser.payout is not None
    assert str(loser.payout) == "0.0000"
    assert loser.quantity == UNPAID


async def test_a_holder_of_both_outcomes_sees_one_worthless_and_one_paid_out_row(
    session: AsyncSession,
) -> None:
    """The result is per outcome, not per market."""
    user_id, _ = await funded(session)
    upstream = await settled_market(
        session, [(user_id, LOSER, UNPAID), (user_id, WINNER, PAID)]
    )

    result = await read_portfolio(session, user_id)

    rows = [p for p in result.positions if p.market_id == upstream.market_id]
    assert [(p.outcome_position, p.result, str(p.payout)) for p in rows] == [
        (LOSER, "worthless", "0.0000"),
        (WINNER, "paid_out", str(PAID)),
    ]


async def test_a_settled_row_has_no_value_and_an_unsettled_row_has_no_result(
    session: AsyncSession,
) -> None:
    user_id, open_market, upstream, _ = await _one_settled_one_open(session)

    result = await read_portfolio(session, user_id)

    settled = row(result, upstream.market_id, WINNER)
    assert (settled.price, settled.value, settled.unrealized_pnl) == (None, None, None)
    unsettled = row(result, open_market.market_id, 0)
    assert (unsettled.result, unsettled.payout) == (None, None)
    assert unsettled.value == HOLDING_VALUE


# =========================================================================
# The totals count the payout once
# =========================================================================
async def test_a_settled_market_adds_nothing_to_positions_value_and_its_payout_once_to_net_worth(
    session: AsyncSession,
) -> None:
    """`positions_value` is the open row's value alone. `net_worth` is every
    entry, the payout included, plus that value: a settled market's shares
    valued at the frozen book on top of the payout would count it twice."""
    user_id, open_market, _, before = await _one_settled_one_open(session)

    result = await read_portfolio(session, user_id)

    open_value = row(result, open_market.market_id, 0).value
    assert result.positions_value == open_value == HOLDING_VALUE
    assert result.balance == before.balance + PAID
    assert result.net_worth == await entries_sum(session, user_id) + open_value


# =========================================================================
# A settled market's book is not valued, so it cannot fail the read
# =========================================================================
@pytest.mark.parametrize(
    "damage", ["b=0.0000", "b=NaN", "one-outcome-row", "q-below-holding"]
)
async def test_a_settled_market_whose_book_cannot_be_priced_does_not_fail_the_read(
    session: AsyncSession, damage: str
) -> None:
    """The damages `test_portfolio_reads.py` fails an unsettled market on,
    applied after settlement. The holder holds only the winner, so dropping
    outcome row 0 leaves their row and breaks the book."""
    user_id, _ = await funded(session)
    upstream = await settled_market(session, [(user_id, WINNER, PAID)])
    ents = entities()

    if damage == "one-outcome-row":
        await session.execute(
            delete(ents.MarketOutcome).where(
                ents.MarketOutcome.market_id == upstream.market_id,
                ents.MarketOutcome.position == LOSER,
            )
        )
        await session.commit()
    elif damage == "q-below-holding":
        await set_q(session, upstream.market_id, [ZERO, Decimal("10.0000")])
    else:
        await session.execute(
            update(ents.MarketBook)
            .where(ents.MarketBook.market_id == upstream.market_id)
            .values(liquidity_b=Decimal(damage.removeprefix("b=")))
        )
        await session.commit()

    result = await read_portfolio(session, user_id)

    winner = row(result, upstream.market_id, WINNER)
    assert (winner.result, winner.payout) == ("paid_out", PAID)
