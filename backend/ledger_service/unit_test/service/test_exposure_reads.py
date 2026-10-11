"""How the exposure read reads. [2.2] #6

The figures are `test_exposure.py`'s. Each structural test names the change
that turns it red:

- **A market with no book writes nothing.** market_service is stubbed to call
  the market published, so a read that opened the book (`ensure_open`, or
  the preview's and snapshot's read-or-open) would succeed and write, and be
  caught writing, rather than fail on the HTTP call and pass by accident.
  DECISIONS.md, "Exposure on a market with no book is a 200 with nothing in
  it, and writes nothing".
- **One statement.** `q` and the pool in one SELECT, so a trade committing
  between two reads cannot put the residue off by its cost. Read the pool
  through `accounts.balance_of` beside the outcome read and a second SELECT
  appears. DECISIONS.md, "Exposure reads `q` and the pool in one statement,
  with no lock".
- **A settled market too.** The result record joins the same statement, so a
  settlement committing between two reads cannot pair a drained pool with no
  record. DECISIONS.md, "A settled market's exposure reads its result, and
  reports no payouts against the drained pool".
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.book_fixtures import TermsStub
from unit_test.exposure_fixtures import (
    EXAMPLE_B,
    EXAMPLE_QUANTITY,
    EXAMPLE_SEED,
    market_with,
    read_exposure,
    row_counts,
)
from unit_test.sell_fixtures import hold
from unit_test.settlement_fixtures import LOSER, PAID, UNPAID, WINNER, settled_market
from unit_test.trade_fixtures import capture_sql, writes


def _selects(statements: list[str]) -> list[str]:
    return [s for s in statements if s.strip().lower().startswith("select")]


async def test_a_market_with_no_book_reads_empty_and_writes_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A published market nobody has touched. Catches: `read_or_open` or
    `ensure_open` used instead of `find`; any call to market_service."""
    stub = TermsStub().install(monkeypatch, published=True)
    before = await row_counts()

    with capture_sql() as statements:
        result = await read_exposure(session, stub.market_id)

    # Before any rollback: the cold path of `ensure_open` commits, and a
    # second session sees a commit whatever this session does next.
    assert writes(statements) == []
    assert await row_counts() == before
    assert stub.calls == 0

    assert result.market_id == stub.market_id
    assert result.opened_at is None
    assert result.state_version is None
    assert result.seed_subsidy is None
    assert result.pool is None
    assert result.outcomes == []
    assert result.payout_exceeds_pool is False


async def test_a_market_with_a_book_is_read_in_one_select(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Catches: the pool read by a second statement."""
    market = await market_with(session, b=EXAMPLE_B, seed=EXAMPLE_SEED)
    await hold(session, market, user_id=user_id, quantity=EXAMPLE_QUANTITY)

    with capture_sql() as statements:
        await read_exposure(session, market.market_id)

    assert writes(statements) == []
    assert len(_selects(statements)) == 1, (
        f"expected one SELECT, got {len(_selects(statements))}:\n{statements}"
    )


async def test_a_settled_market_is_still_read_in_one_select(
    session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    """Catches: `market_results` read by a second statement."""
    market = await settled_market(
        session, [(other_user_id, LOSER, UNPAID), (user_id, WINNER, PAID)]
    )

    with capture_sql() as statements:
        await read_exposure(session, market.market_id)

    assert writes(statements) == []
    assert len(_selects(statements)) == 1, (
        f"expected one SELECT, got {len(_selects(statements))}:\n{statements}"
    )
