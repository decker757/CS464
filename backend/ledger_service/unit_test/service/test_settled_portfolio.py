"""The portfolio after settlement. [3.4] #12, [B-1] #359

DECISIONS.md: "A settled position shows `result` and `payout`, and its value
fields are null", "A settled row's payout is its quantity, and its result comes
from `ledger.market_results`, joined in the portfolio's one statement", and
"`result` and `payout` are on every portfolio row, null until the market is
settled". The wire shape is `controller/test_portfolio_routes.py`'s.

Settled markets come from `settlement_fixtures.py`: positions inserted on a
warm book, then the real `pay_out`. The winner is outcome position 1, never
0, so a read that takes a market's first outcome as its winner fails here.
Open markets come from a real buy, valued at `HOLDING_VALUE`.

The P&L section below is the exception: a seeded position has a `cost_basis`
no entry ever paid for, which nothing derived from the entries can reconcile
against, so those markets are bought into for real and only then settled.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import drop_outcome_row, set_b, set_q
from unit_test.portfolio_fixtures import (
    HOLDING_VALUE,
    entries_sum,
    funded,
    market_at,
    read_portfolio,
    row,
)
from unit_test.sell_fixtures import BASIS, hold, version_of
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
from unit_test.trade_fixtures import ZERO, buy, entities

# Bought for real in the P&L section, on `settlement_fixtures`' b = 137 book.
BOUGHT = Decimal("41.0000")


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

    if damage == "one-outcome-row":
        await drop_outcome_row(session, upstream.market_id, LOSER)
    elif damage == "q-below-holding":
        await set_q(session, upstream.market_id, [ZERO, Decimal("10.0000")])
    else:
        await set_b(session, upstream.market_id, Decimal(damage.removeprefix("b=")))

    result = await read_portfolio(session, user_id)

    winner = row(result, upstream.market_id, WINNER)
    assert (winner.result, winner.payout) == ("paid_out", PAID)


# =========================================================================
# What a settled market banks. [B-1] #359
# =========================================================================
async def _bought_then_settled(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    outcome: int,
    quantity: Decimal = BOUGHT,
) -> tuple[SettleUpstream, Decimal]:
    """A market this user really bought `outcome` in, then settled — for
    `WINNER`, as every market here is. Returns the market and what the shares
    cost, so a test can state the P&L in terms of money that actually moved.
    `gate_open` answers the trade's gate; the settlement reads the approved
    body."""
    upstream = SettleUpstream()
    await warm(session, upstream)
    trade = await buy(
        session,
        upstream,
        user_id=user_id,
        outcome=outcome,
        quantity=quantity,
        state_version=await version_of(session, upstream.market_id),
        client_key=f"pnl-{uuid.uuid4()}",
        transport=upstream.gate_open,
    )
    await settle(session, upstream)
    return upstream, -trade.total


@pytest.mark.parametrize("outcome", [WINNER, LOSER], ids=["paid-out", "worthless"])
async def test_a_settled_holding_banks_its_payout_against_what_it_cost(
    session: AsyncSession, outcome: int
) -> None:
    """"Settled positions are accounted for, so a user who has been paid out
    sees that reflected" (#359). The winner banks its payout less its cost;
    the loser banks the whole cost as a loss. Neither is still riding, so
    `unrealized_pnl` is flat either way."""
    user_id, _ = await funded(session)
    upstream, cost = await _bought_then_settled(session, user_id, outcome=outcome)

    result = await read_portfolio(session, user_id)

    settled = row(result, upstream.market_id, outcome)
    assert settled.cost_basis == cost
    assert result.realized_pnl == settled.payout - cost
    assert str(result.unrealized_pnl) == "0.0000"
    assert result.total_pnl == result.realized_pnl


async def test_net_worth_less_starting_credits_is_total_pnl_across_both_kinds(
    session: AsyncSession,
) -> None:
    """#359's reconciliation, over a user holding one position nobody has
    closed and one the ledger has paid out.

    Each half has to be right on its own: the open market contributes only
    unrealised P&L, the settled one only realised, and the two must come to
    the difference between what this user has now and what they were given.
    A realised figure that forgot the cost still tied up in the open
    position fails here.
    """
    user_id, credits = await funded(session)
    open_market = await market_at(session)
    await hold(session, open_market, user_id=user_id)
    settled, cost = await _bought_then_settled(session, user_id, outcome=WINNER)

    result = await read_portfolio(session, user_id)

    assert len(result.positions) == 2
    assert result.starting_credits == credits
    assert result.realized_pnl == BOUGHT - cost
    assert result.unrealized_pnl == HOLDING_VALUE - BASIS
    assert result.total_pnl == result.realized_pnl + result.unrealized_pnl
    assert result.net_worth - result.starting_credits == result.total_pnl
    assert row(result, settled.market_id, WINNER).payout == BOUGHT
