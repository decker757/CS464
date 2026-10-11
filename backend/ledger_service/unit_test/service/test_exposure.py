"""What resolving a market would cost. [2.2] #6

`service/exposure.py::exposure_of_market(session, *, market_id)`. Each test
names the change that turns it red. The structure of the read — one
statement, no writes on a cold market — is `test_exposure_reads.py`'s.

Every figure expected here is built from what the trades returned, never from
a fresh LMSR call (`exposure_fixtures.py`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.exposure_fixtures import (
    EXAMPLE_B,
    EXAMPLE_POOL,
    EXAMPLE_QUANTITY,
    EXAMPLE_RESIDUE,
    EXAMPLE_SEED,
    assert_book_terms,
    book_from_another_session,
    by_position,
    market_with,
    pool_after,
    read_exposure,
)
from unit_test.sell_fixtures import hold, sell
from unit_test.settlement_fixtures import (
    LOSER,
    PAID,
    SUBSIDY,
    UNPAID,
    WINNER,
    positions_and_q,
    settled_market,
)

B = Decimal("100.0000")
ZERO = Decimal("0")


# =========================================================================
# The flag, against the pool
# =========================================================================
async def test_a_payout_the_pool_covers_does_not_raise_the_flag(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Seed 100, one buy of 150. The payout is over the seed and under the
    pool. Catches: the flag comparing a payout with the seed."""
    seed = Decimal("100.0000")
    quantity = Decimal("150.0000")
    market = await market_with(session, b=B, seed=seed)
    await assert_book_terms(market.market_id, b=B, seed=seed)

    bought = await hold(session, market, user_id=user_id, quantity=quantity)
    pool = pool_after(seed, [bought])

    result = await read_exposure(session, market.market_id)

    assert quantity > seed, "the payout must be over the seed for this to test anything"
    assert by_position(result.outcomes)[0].residue_if_wins == pool - quantity
    assert by_position(result.outcomes)[0].residue_if_wins > ZERO
    assert result.payout_exceeds_pool is False


async def test_a_payout_the_pool_cannot_cover_raises_the_flag(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """docs/api/ledger-service.md's worked example: seed 50, b 100, one buy
    of 500. Catches: a flag that never fires; a residue with its sign flipped;
    every row given `pool - max payout` instead of its own."""
    market = await market_with(session, b=EXAMPLE_B, seed=EXAMPLE_SEED)
    await assert_book_terms(market.market_id, b=EXAMPLE_B, seed=EXAMPLE_SEED)

    bought = await hold(session, market, user_id=user_id, quantity=EXAMPLE_QUANTITY)
    pool = pool_after(EXAMPLE_SEED, [bought])
    residue = pool - EXAMPLE_QUANTITY
    # The documented literals, reached from the returned cost.
    assert pool == EXAMPLE_POOL
    assert residue == EXAMPLE_RESIDUE

    result = await read_exposure(session, market.market_id)
    outcomes = by_position(result.outcomes)

    assert result.pool == pool
    assert outcomes[0].payout_if_wins == Decimal("500.0000")
    assert outcomes[0].residue_if_wins == residue
    assert outcomes[1].residue_if_wins == pool
    assert result.payout_exceeds_pool is True


# =========================================================================
# Shares outstanding, and which book this is
# =========================================================================
async def test_shares_outstanding_are_every_holders_shares_in_this_market(
    session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    """Two holders, buys in both outcomes and a partial sell, beside a second
    market with a different `q`. Catches: one user's positions reported;
    another market's `q`; `state_version` or `opened_at` missing or constant."""
    market = await market_with(session, b=B, seed=Decimal("100.0000"))
    await hold(session, market, user_id=user_id, quantity=Decimal("60.0000"), outcome=0)
    await hold(
        session, market, user_id=other_user_id, quantity=Decimal("40.0000"), outcome=1
    )
    await hold(
        session, market, user_id=other_user_id, quantity=Decimal("25.0000"), outcome=0
    )
    await sell(session, market, user_id=user_id, quantity=Decimal("15.0000"), outcome=0)

    elsewhere = await market_with(session, b=B, seed=Decimal("80.0000"))
    await hold(
        session, elsewhere, user_id=user_id, quantity=Decimal("33.0000"), outcome=1
    )

    result = await read_exposure(session, market.market_id)
    book = await book_from_another_session(market.market_id)
    outcomes = by_position(result.outcomes)

    assert len(result.outcomes) == 2
    assert outcomes[0].outcome_id == market.outcomes[0]
    assert outcomes[1].outcome_id == market.outcomes[1]
    assert outcomes[0].shares_outstanding == Decimal("70.0000")  # 60 + 25 - 15
    assert outcomes[1].shares_outstanding == Decimal("40.0000")
    assert result.state_version == book.state_version
    assert result.state_version == 4, "four trades, so a constant 0 cannot pass"
    assert result.opened_at == book.opened_at


# =========================================================================
# The pool
# =========================================================================
async def test_the_pool_is_the_seed_plus_buys_less_sells_and_its_own_markets(
    session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    """Two markets with different pools in one database, each read on its
    own. Catches: the pool reported as `seed_subsidy`; sells ignored; the
    pool subquery failing to correlate on `pool_account_id`."""
    seed = Decimal("100.0000")
    other_seed = Decimal("70.0000")
    market = await market_with(session, b=B, seed=seed)
    elsewhere = await market_with(session, b=B, seed=other_seed)
    await assert_book_terms(market.market_id, b=B, seed=seed)
    await assert_book_terms(elsewhere.market_id, b=B, seed=other_seed)

    trades = [
        await hold(session, market, user_id=user_id, quantity=Decimal("120.0000")),
        await hold(
            session, market, user_id=other_user_id, quantity=Decimal("30.0000"), outcome=1
        ),
        await sell(session, market, user_id=user_id, quantity=Decimal("50.0000")),
    ]
    other_trades = [
        await hold(session, elsewhere, user_id=other_user_id, quantity=Decimal("45.0000"))
    ]
    pool = pool_after(seed, trades)
    other_pool = pool_after(other_seed, other_trades)

    result = await read_exposure(session, market.market_id)
    other_result = await read_exposure(session, elsewhere.market_id)

    assert result.seed_subsidy == seed
    assert result.pool == pool
    assert other_result.seed_subsidy == other_seed
    assert other_result.pool == other_pool


# =========================================================================
# A settled market
# =========================================================================
async def test_a_settled_market_reads_its_result_and_no_figures_against_the_drained_pool(
    session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    """Settled through the real `pay_out`, which empties the pool and leaves
    `q`. DECISIONS.md, "A settled market's exposure reads its result, and
    reports no payouts against the drained pool".
    Catches: `market_results` ignored."""
    market = await settled_market(
        session, [(other_user_id, LOSER, UNPAID), (user_id, WINNER, PAID)]
    )
    _, q = await positions_and_q(session, market.market_id)

    result = await read_exposure(session, market.market_id)
    outcomes = by_position(result.outcomes)

    assert result.payout_exceeds_pool is False
    assert result.pool is None
    for position in (LOSER, WINNER):
        assert outcomes[position].payout_if_wins is None, position
        assert outcomes[position].residue_if_wins is None, position
    assert result.result == "settled"
    assert result.winning_outcome_id == market.winner
    assert [outcomes[position].shares_outstanding for position in (0, 1)] == q
    assert q[LOSER] == UNPAID and q[WINNER] == PAID, "q is what was held"
    assert result.seed_subsidy == SUBSIDY
