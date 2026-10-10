"""How the portfolio reads. [T-4] #24

The grant, the absence of a lock, the absence of a market_service hop, the one
statement, and the damaged book. The figures are `test_portfolio.py`'s.

Each structural test names the change that turns it red:

- **No lock.** Another session holds `FOR UPDATE` on everything a trade in
  flight holds — the book row, the outcome rows, the caller's position rows
  and the caller's account row — and the read must finish under a 2 s
  `lock_timeout`. A plain SELECT does not wait on row locks. Adding
  `.with_for_update(of=MarketBook)` to the read turns this red either way:
  against the current outer-join query, every table past the anchor sits on
  the nullable side of a left join, so Postgres refuses the lock outright
  with `FeatureNotSupportedError` before it can even queue. If the query is
  ever rewritten so a lock is accepted, it queues on the held rows instead
  and fails on `lock_timeout` (`LockNotAvailable`). `FOR SHARE` and
  `FOR KEY SHARE` conflict with `FOR UPDATE` too, and so fail the same way.
- **One statement.** After the grant, exactly one statement mentions
  `ledger.entries`, `ledger.positions` and `ledger.market_books`, and it is
  the same statement. Read the balance through `accounts.balance_of` beside
  the positions query and a second `entries` statement appears; read a book
  per market through `book_prices` and a second `market_books` one does.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.portfolio_fixtures import (
    ADR_B,
    funded,
    market_at,
    read_portfolio,
)
from unit_test.sell_fixtures import HELD, SOLD, hold
from unit_test.trade_fixtures import (
    TIMEOUT,
    capture_sql,
    entities,
    entry_count,
    errors,
    grants,
    session_factory,
    set_q,
    transaction_count,
)

_LOCK_TIMEOUT = "2s"


def _schema() -> str:
    from core.database import SCHEMA  # noqa: PLC0415

    return SCHEMA


async def _holder(session: AsyncSession):
    """A market opened at zero, and a funded user holding #23's 54.3333."""
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    await hold(session, upstream, user_id=user_id)
    return upstream, user_id


# =========================================================================
# The grant
# =========================================================================
async def test_the_first_read_mints_the_grant_once_and_the_second_does_not(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """A user nobody has read before gets their starting credits on their
    first portfolio read, as the balance route would, and never again."""
    user_id = uuid.uuid4()
    key = grants().grant_key(user_id)
    assert await transaction_count(session, key=key) == 0

    first = await read_portfolio(session, user_id)

    assert first.balance == starting_credits
    assert first.net_worth == starting_credits
    assert first.positions == []
    assert await transaction_count(session, key=key) == 1
    entries = await entry_count(session)

    second = await read_portfolio(session, user_id)

    assert second.balance == starting_credits
    assert second.account_id == first.account_id
    assert await transaction_count(session, key=key) == 1
    assert await entry_count(session) == entries


# =========================================================================
# No lock, no hop, one statement
# =========================================================================
async def _lock_what_a_trade_holds(
    other: AsyncSession, market_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    ents = entities()
    await other.execute(
        select(ents.MarketBook.market_id)
        .where(ents.MarketBook.market_id == market_id)
        .with_for_update()
    )
    await other.execute(
        select(ents.MarketOutcome.outcome_id)
        .where(ents.MarketOutcome.market_id == market_id)
        .with_for_update()
    )
    await other.execute(
        select(ents.Position.outcome_id)
        .where(ents.Position.user_id == user_id)
        .with_for_update()
    )
    await other.execute(
        select(ents.Account.id)
        .where(
            ents.Account.kind == ents.AccountKind.USER,
            ents.Account.owner_id == user_id,
        )
        .with_for_update()
    )


async def test_the_read_takes_no_lock_while_a_trade_holds_the_book(
    session: AsyncSession,
) -> None:
    upstream, user_id = await _holder(session)

    async with session_factory()() as other:
        await _lock_what_a_trade_holds(other, upstream.market_id, user_id)
        try:
            await session.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
            result = await asyncio.wait_for(
                read_portfolio(session, user_id), timeout=TIMEOUT
            )
        finally:
            await other.rollback()

    assert [p.quantity for p in result.positions] == [HELD]


async def test_the_read_makes_no_call_to_market_service(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any HTTP request from any `httpx.AsyncClient` fails the read and is
    recorded, so a caught failure still shows up. Two markets are held, one
    of them closed, so a per-market status hop has somewhere to happen."""
    upstream, user_id = await _holder(session)
    closed = await market_at(session, ADR_B)
    await hold(session, closed, user_id=user_id, quantity=SOLD)
    closed.closes()
    upstream.calls = closed.calls = 0

    sent: list[str] = []

    async def refuse(self, request, *args, **kwargs):  # noqa: ANN001
        sent.append(str(request.url))
        raise AssertionError(f"the portfolio called {request.url}")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)

    result = await read_portfolio(session, user_id)

    assert sent == []
    assert (upstream.calls, closed.calls) == (0, 0)
    assert len(result.positions) == 2


def _mentioning(statements: list[str], table: str) -> list[str]:
    return [s for s in statements if table in s]


async def test_after_the_grant_the_balance_and_positions_are_one_statement(
    session: AsyncSession,
) -> None:
    """The balance, the positions, `q`, `b` and `state_version` in one
    snapshot, so a trade committing mid-read cannot show its debit without
    its shares. Two markets, so a per-market book read is two statements."""
    _, user_id = await _holder(session)
    second = await market_at(session, ADR_B)
    await hold(session, second, user_id=user_id, quantity=SOLD)
    schema = _schema()

    with capture_sql() as statements:
        await read_portfolio(session, user_id)

    entries = _mentioning(statements, f"{schema}.entries")
    assert len(entries) == 1, f"the balance was read {len(entries)} times:\n{entries}"
    for table in ("positions", "market_books", "market_outcomes"):
        found = _mentioning(statements, f"{schema}.{table}")
        assert found == entries, (
            f"`{table}` is not read in the balance's statement:\n{statements}"
        )


# =========================================================================
# A damaged book fails the whole read
# =========================================================================
async def _set_b(session: AsyncSession, market_id: uuid.UUID, b: Decimal) -> None:
    book = entities().MarketBook
    await session.execute(
        update(book).where(book.market_id == market_id).values(liquidity_b=b)
    )
    await session.commit()


async def _drop_outcome_row(session: AsyncSession, market_id: uuid.UUID, position: int) -> None:
    outcome = entities().MarketOutcome
    await session.execute(
        delete(outcome).where(outcome.market_id == market_id, outcome.position == position)
    )
    await session.commit()


def _assert_market_book_incomplete(raised: pytest.ExceptionInfo) -> None:
    assert isinstance(raised.value, errors().LedgerError), (
        f"raised an unmapped {type(raised.value).__name__}: {raised.value!r}"
    )
    assert raised.value.code == "market_book_incomplete"
    assert raised.value.status_code == 500


@pytest.mark.parametrize("damage", ["b=0.0000", "b=-100.0000", "b=NaN", "one-outcome-row"])
async def test_a_book_that_cannot_be_priced_fails_the_whole_read(
    session: AsyncSession, damage: str
) -> None:
    """A healthy market is held beside the damaged one, so a read that skipped
    the damaged row would return something. It must return nothing."""
    _, user_id = await _holder(session)
    damaged = await market_at(session, ADR_B)
    await hold(session, damaged, user_id=user_id, quantity=SOLD)

    if damage == "one-outcome-row":
        await _drop_outcome_row(session, damaged.market_id, 1)
    else:
        await _set_b(session, damaged.market_id, Decimal(damage.removeprefix("b=")))

    with pytest.raises(Exception) as raised:
        await read_portfolio(session, user_id)

    _assert_market_book_incomplete(raised)


async def test_a_position_above_q_fails_the_whole_read(session: AsyncSession) -> None:
    """The one test that writes `q` directly: 10.0000 under a holding of
    54.3333. `InsufficientSharesOutstanding` would be a 409 blaming a request
    this caller never made; the read remaps it."""
    upstream, user_id = await _holder(session)
    healthy = await market_at(session, ADR_B)
    await hold(session, healthy, user_id=user_id, quantity=SOLD)
    await set_q(session, upstream.market_id, [Decimal("10.0000"), Decimal("0")])

    with pytest.raises(Exception) as raised:
        await read_portfolio(session, user_id)

    _assert_market_book_incomplete(raised)
