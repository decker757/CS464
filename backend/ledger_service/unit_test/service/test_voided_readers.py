"""What the portfolio and the history show once a market is voided. [BE] #221

ADR 0025, "What readers show", and DECISIONS.md (on #416), "A voided row's
`payout` is its refund, joined in the portfolio's statement only". Every
market is traded through the real trade path and voided through the real
`void_market`, and every figure is `test_void.py`'s, worked outside the code.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from unit_test.history_fixtures import read_history, trade_fields, transaction_ids
from unit_test.portfolio_fixtures import read_portfolio, row
from unit_test.settlement_fixtures import account_id_of, legs_of, transaction_by_key
from unit_test.trade_fixtures import entities
from unit_test.void_fixtures import VoidUpstream, open_book, refund_key, trade, void

ONE = Decimal("1.0000")


async def _bought_4_sold_2(session: AsyncSession) -> tuple[VoidUpstream, uuid.UUID]:
    """ADR 0025's lone trader at `b = 1`: buy 4 (3.3251), sell 2 (1.8912), and
    the void refunds the 2 left 1.4337, which is neither their quantity nor
    their cost basis at average cost."""
    upstream = VoidUpstream(b=ONE)
    await open_book(session, upstream)
    trader = uuid.uuid4()
    await trade(session, upstream, trader, outcome=0, side="buy", quantity="4")
    await trade(session, upstream, trader, outcome=0, side="sell", quantity="2")
    upstream.closes()
    await void(session, upstream)
    return upstream, trader


async def _refund_leg(
    session: AsyncSession, upstream: VoidUpstream, user_id: uuid.UUID, position: int
) -> Decimal:
    """The holder's own leg of their refund, read from the ledger."""
    refund = await transaction_by_key(
        session,
        refund_key(upstream.market_id, user_id=user_id, outcome_id=upstream.outcomes[position]),
    )
    assert refund is not None, "the holder was never refunded"
    legs = await legs_of(session, refund.id)
    return legs[await account_id_of(session, entities().AccountKind.USER, user_id)]


# =========================================================================
# The portfolio
# =========================================================================
async def test_a_voided_row_is_refunded_and_its_payout_is_its_refund(
    session: AsyncSession,
) -> None:
    """Ruling A: `result` is `refunded`, and `payout` is what the row's
    `void_refund` paid the holder, 1.4337: not its quantity, 2, and not its
    cost basis. `price`, `value` and `unrealized_pnl` are null, as on a
    settled row."""
    upstream, trader = await _bought_4_sold_2(session)

    voided = row(await read_portfolio(session, trader), upstream.market_id, 0)

    assert voided.result == "refunded"
    assert voided.payout == Decimal("1.4337")
    assert voided.payout == await _refund_leg(session, upstream, trader, 0)
    assert voided.payout not in (voided.quantity, voided.cost_basis)
    assert (voided.price, voided.value, voided.unrealized_pnl) == (None, None, None)


async def test_a_refund_that_rounded_to_nothing_is_refunded_with_a_payout_of_zero(
    session: AsyncSession,
) -> None:
    """`test_void.py`'s market whose every refund rounds to zero: H keeps one
    bottom share worth about 1.6e-13, so the void wrote H no transaction.
    The row is still `refunded`, from the record's `kind`, with a payout of
    `0.0000` at scale 4: the join alone cannot tell it from a settled loser
    (DECISIONS.md's note on ruling A)."""
    upstream = VoidUpstream(b=ONE)
    await open_book(session, upstream)
    o, h = uuid.uuid4(), uuid.uuid4()
    await trade(session, upstream, o, outcome=0, side="buy", quantity="30")
    await trade(session, upstream, h, outcome=1, side="buy", quantity="40")
    await trade(session, upstream, h, outcome=1, side="sell", quantity="39")
    await trade(session, upstream, o, outcome=0, side="sell", quantity="30")
    upstream.closes()
    await void(session, upstream)
    no_refund = await transaction_by_key(
        session, refund_key(upstream.market_id, user_id=h, outcome_id=upstream.outcomes[1])
    )
    assert no_refund is None, "the scenario was meant to write H no refund"

    voided = row(await read_portfolio(session, h), upstream.market_id, 1)

    assert voided.result == "refunded"
    assert voided.payout is not None
    assert str(voided.payout) == "0.0000"


async def test_a_voided_market_adds_nothing_to_positions_value_and_its_refund_is_banked(
    session: AsyncSession,
) -> None:
    """ADR 0025, "P&L needs no change": the refund is in the balance, the row
    adds nothing to `positions_value`, and the trader who bought 4 and sold 2
    is down exactly 0.0002 on their grant, all of it realised."""
    _, trader = await _bought_4_sold_2(session)

    result = await read_portfolio(session, trader)

    assert str(result.positions_value) == "0.0000"
    assert result.total_pnl == result.realized_pnl == Decimal("-0.0002")
    assert str(result.unrealized_pnl) == "0.0000"


# =========================================================================
# The history
# =========================================================================
async def test_a_refund_is_a_void_refund_row_naming_its_market_outcome_and_shares(
    session: AsyncSession,
) -> None:
    """ADR 0025, "History": one `void_refund` row per refunded position, with
    `market_id`, `outcome_id` and `quantity`, the shares refunded; `side` and
    `average_price` null. A holder of 12 of outcome 1 at `b = 10` bought for
    7.7014 and is refunded 7.7014, so the row leaves the balance at the grant.
    Outcome position 1, so a row naming the market's first outcome is caught."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    holder = uuid.uuid4()
    await trade(session, upstream, holder, outcome=1, side="buy", quantity="12")
    upstream.closes()
    await void(session, upstream)
    refund = await transaction_by_key(
        session,
        refund_key(upstream.market_id, user_id=holder, outcome_id=upstream.outcomes[1]),
    )

    rows = (await read_history(session, holder)).rows

    assert transaction_ids(rows)[0] == refund.id
    # The pool keeps the seed, so this void also wrote a residue, between the
    # pool and PLATFORM: not a row of this holder's.
    kinds = {row.entry.transaction.kind for row in rows}
    assert entities().TransactionKind.VOID_RESIDUE not in kinds
    newest = rows[0]
    assert newest.entry.transaction.kind == entities().TransactionKind.VOID_REFUND
    assert newest.entry.amount == Decimal("7.7014")
    assert newest.balance_after == get_settings().starting_credits
    assert trade_fields(newest) == (
        upstream.market_id,
        upstream.outcomes[1],
        None,
        Decimal("12.0000"),
        None,
    )
