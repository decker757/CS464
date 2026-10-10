"""Fixtures the [T-4] #24 test files share.

Built on `sell_fixtures.py` and `trade_fixtures.py` rather than beside them:
the same `Upstream`, the same `open_at_zero`, `hold` and `sell`, the same lazy
module handles. What is added here is the one driver for the portfolio read,
a market at a chosen `b`, and the expected valuation restated from the
criterion.

**Every holding comes from a real trade on a book opened at zero by its first
touch.** Never `warm()`, never a position inserted by hand. The one test that
writes `q` directly is the position-above-`q` test, in its own body.

**The numbers are ADR 0018's and #23's, reached through real trades.** At
`b = 100`, 500 YES bought on a book at zero costs 431.3569 and sells back for
431.3568. At `b = 137`, #23's 54.3333 costs 29.8428 and sells back for
29.8427. `unit_test/core/test_portfolio_figures.py` holds every literal here
against `core/lmsr.py`, so none of them can drift from the engine without a
test naming which one moved.

Nothing here imports a project module at module scope, for the reason
`trade_fixtures.py` gives: `service/portfolio.py` does not exist yet, and a
module-scope import would fail every file as one collection error.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.sell_fixtures import open_at_zero
from unit_test.trade_fixtures import (
    B,
    QUANTUM,
    ZERO,
    Upstream,
    entities,
    errors,
    fund,
    lmsr,
)

# --- ADR 0018's worked example, pinned ------------------------------------
ADR_B = Decimal("100.0000")
ADR_QUANTITY = Decimal("500.0000")
ADR_BASIS = Decimal("431.3569")
ADR_VALUE = Decimal("431.3568")
ADR_PNL = Decimal("-0.0001")
ADR_PRICE = Decimal("0.9933")
# 431.3569 / 500 = 0.8627138: half-up and floor agree here, so the average
# entry rounding is tested on #23's figures instead.
ADR_AVERAGE = Decimal("0.8627")

# --- ADR 0018's complete set: 500 YES then 500 NO at b = 100 --------------
SET_NO_BASIS = Decimal("68.6432")
SET_VALUES = (Decimal("68.6431"), Decimal("431.3568"))
SET_TOTAL = Decimal("499.9999")
SET_SEPARATE_TOTAL = Decimal("137.2862")  # each sold alone: the rejected rule

# --- #23's holding at b = 137 ---------------------------------------------
HOLDING_VALUE = Decimal("29.8427")
HOLDING_PRICE = Decimal("0.5979")
# 29.8428 / 54.3333 = 0.54925…: half-up 0.5493, floor 0.5492.
HOLDING_AVERAGE = Decimal("0.5493")
# After selling 10.0000: 44.3333 held at 24.3503, q = (44.3333, 0).
REMAINING_VALUE = Decimal("23.9521")
REMAINING_AVERAGE = Decimal("0.5493")

# --- the exact-tick book: A buys 13.3333 of outcome 1, then B buys 26.6666
# of outcome 0. At q = (0, L), buying 2L costs exactly L, and selling it back
# pays exactly L. On this engine at b = 137 both raw figures are 13.3333 to
# all fifty digits.
EXACT_TICK_LEAD = Decimal("13.3333")
EXACT_TICK_BUY = Decimal("26.6666")
EXACT_TICK_BASIS = Decimal("13.3333")

# --- positions built from several buys, nobody else trading, b = 137 -------
TWO_BUYS = (Decimal("13.0000"), Decimal("13.0000"))
TWO_BUYS_BASIS = Decimal("13.6160")
TWO_BUYS_VALUE = Decimal("13.6158")
THREE_BUYS = (Decimal("13.0000"), Decimal("13.0000"), Decimal("13.0411"))
THREE_BUYS_BASIS = Decimal("20.9068")
THREE_BUYS_VALUE = Decimal("20.9065")

# --- dust: 54.3333 bought, 54.3332 sold, 0.0001 left at a basis of 0.0001 --
DUST = Decimal("0.0001")
DUST_SOLD = Decimal("54.3332")
# Then 54.3333 of outcome 1 bought at q = (0.0001, 0). Sold in sequence, the
# dust step is refused and leaves q alone, so outcome 1 sells at
# (0.0001, 54.3333) for 29.8426. A step that moved the book to (0, 54.3333)
# first would pay 29.8427.
AFTER_DUST_VALUE = Decimal("29.8426")
AFTER_MOVED_DUST_VALUE = Decimal("29.8427")

# --- two markets, one b each ----------------------------------------------
# 54.3333 at b = 100 sells back for 30.8122; at b = 137, 29.8427. A read that
# priced one market against the other's b swaps them.
OTHER_B_VALUE = Decimal("30.8122")

POSITION_FIELDS = {
    "market_id",
    "outcome_id",
    "outcome_position",
    "quantity",
    "cost_basis",
    "average_entry_price",
    "price",
    "value",
    "unrealized_pnl",
    "result",
    "payout",
    "state_version",
}
PORTFOLIO_FIELDS = {
    "user_id",
    "account_id",
    "balance",
    "positions_value",
    "net_worth",
    "positions",
}


# --- lazy handles --------------------------------------------------------
def portfolio():
    """`service/portfolio.py`, [T-4] #24's own module. Does not exist yet."""
    from service import portfolio  # noqa: PLC0415

    return portfolio


def market_book_incomplete():
    return errors().MarketBookIncomplete


# --- driving the thing under test ----------------------------------------
async def read_portfolio(session: AsyncSession, user_id: uuid.UUID):
    """The portfolio read, with the signature agreed for #24. The single
    place it is written down."""
    return await portfolio().portfolio_of_user(session, user_id)


# --- markets and traders --------------------------------------------------
async def market_at(
    session: AsyncSession, b: Decimal = B, *, outcomes: int = 2
) -> Upstream:
    """A market opened at zero by its first touch, at liquidity `b`."""
    upstream = Upstream(outcomes=outcomes)
    upstream.body["liquidity_b"] = str(b)
    await open_at_zero(session, upstream)
    return upstream


async def funded(session: AsyncSession) -> tuple[uuid.UUID, Decimal]:
    """A user granted their starting credits, and the amount. The ADR's
    complete set costs 500.0001, so a grant below that cannot fund it."""
    user_id = uuid.uuid4()
    credits = await fund(session, user_id)
    assert credits > Decimal("600"), (
        f"STARTING_CREDITS is {credits}; these tests buy up to 500.0001"
    )
    return user_id, credits


# --- reading the result ----------------------------------------------------
def row(result, market_id: uuid.UUID, position: int):
    """The one row for this market and outcome position."""
    matches = [
        p
        for p in result.positions
        if p.market_id == market_id and p.outcome_position == position
    ]
    assert len(matches) == 1, f"expected one row for {market_id}/{position}, got {matches}"
    return matches[0]


async def entries_sum(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    """`SUM(amount)` over this user's entries, straight from the table."""
    ents = entities()
    session.expire_all()
    return (
        await session.execute(
            select(func.coalesce(func.sum(ents.Entry.amount), ZERO))
            .join(ents.Account, ents.Account.id == ents.Entry.account_id)
            .where(
                ents.Account.kind == ents.AccountKind.USER,
                ents.Account.owner_id == user_id,
            )
        )
    ).scalar_one()


# --- the expected valuation, restated from the criterion -------------------
def sequence_values(
    q: Sequence[Decimal], b: Decimal, holdings: Sequence[Decimal]
) -> list[Decimal]:
    """One sale per held outcome, in position order, each on the `q` the last
    one left; floored; a sub-tick sale is `0.0000` and leaves `q` alone.

    Restated from the #24 criterion with `core/lmsr.py` alone, so the tests
    check the implementation against the sentence and not against itself.
    """
    current = list(q)
    values: list[Decimal] = []
    for index, held in enumerate(holdings):
        if held == 0:
            values.append(Decimal("0.0000"))
            continue
        delta = [ZERO] * len(current)
        delta[index] = -held
        raw = lmsr().cost_to_trade(current, b, delta).copy_abs()
        proceeds = raw.quantize(QUANTUM, rounding=ROUND_FLOOR)
        values.append(proceeds)
        if proceeds > 0:
            current[index] -= held
    return values
