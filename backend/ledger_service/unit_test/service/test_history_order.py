"""A user's history lists their trades in the order they committed. #187.

Two trades by one user in two markets hold two different book locks, so both
can reach `posting.post` at once and queue on the user's account lock. The
trade that took its time first can commit second. Each race here arranges
exactly that, deterministically: the first trade is held at the account locks,
after the trade path has taken its time, while the second runs to its commit.
Move `post`'s stamp back to before `accounts.lock` and both fail.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.history_fixtures import read_history
from unit_test.sell_fixtures import HELD, hold, sell
from unit_test.trade_fixtures import (
    Q,
    SMALL_QUANTITY,
    TIMEOUT,
    Upstream,
    accounts_module,
    balance_of_user,
    buy,
    expected_total,
    fund,
    largest_buy_within,
    q_of,
    session_factory,
    warm,
)

Trade = Callable[[AsyncSession], Awaitable[object]]


async def _commit_in_reverse(
    monkeypatch: pytest.MonkeyPatch, *, stamped_first: Trade, stamped_second: Trade
) -> tuple[object, object]:
    """Run both trades, and let `stamped_first` take its account locks only
    once `stamped_second` has committed.

    Both parties connect and meet at a barrier first (ADR 0015). The first
    trade's time is taken at `trading.execute`'s step 10, before it reaches
    the locks, so it is always the earlier of the two.
    """
    factory = session_factory()
    start = asyncio.Barrier(2)
    first_at_the_locks = asyncio.Event()
    second_committed = asyncio.Event()
    held_sessions: set[AsyncSession] = set()
    real_lock = accounts_module().lock

    async def lock_after_the_second_commits(own: AsyncSession, account_ids) -> None:
        if own in held_sessions:
            first_at_the_locks.set()
            await second_committed.wait()
        await real_lock(own, account_ids)

    monkeypatch.setattr(accounts_module(), "lock", lock_after_the_second_commits)

    async def first():
        async with factory() as own:
            held_sessions.add(own)
            await own.connection()
            await start.wait()
            return await stamped_first(own)

    async def second():
        async with factory() as own:
            await own.connection()
            await start.wait()
            await first_at_the_locks.wait()
            try:
                return await stamped_second(own)
            finally:
                second_committed.set()

    async with asyncio.timeout(TIMEOUT):
        first_result, second_result = await asyncio.gather(first(), second())
    return first_result, second_result


async def test_a_trade_that_commits_second_is_listed_above_the_one_that_committed_first(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#187's second criterion: on every account, feed order is commit order."""
    first_market, second_market = Upstream(), Upstream()
    user_id = uuid.uuid4()
    await warm(session, first_market)
    await warm(session, second_market)
    await fund(session, user_id)

    committed_second, committed_first = await _commit_in_reverse(
        monkeypatch,
        stamped_first=lambda own: buy(
            own, first_market, user_id=user_id, quantity=SMALL_QUANTITY
        ),
        stamped_second=lambda own: buy(
            own, second_market, user_id=user_id, quantity=SMALL_QUANTITY
        ),
    )

    newest, older, _grant = (await read_history(session, user_id)).rows
    assert newest.entry.transaction_id == committed_second.transaction_id
    assert older.entry.transaction_id == committed_first.transaction_id
    assert newest.entry.created_at > older.entry.created_at


async def test_no_running_balance_is_negative_when_a_sale_pays_for_a_buy_stamped_before_it(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#187's third criterion, its -2 example at real prices.

    The buy costs more than the user holds, and less than they hold once the
    sale has paid out. It passes the overdraft check because the sale commits
    first. Listed by a time taken before the lock, the buy would come first
    and show a balance the user never had.
    """
    buy_market, sell_market = Upstream(), Upstream()
    user_id = uuid.uuid4()
    await warm(session, buy_market)
    await warm(session, sell_market)
    await fund(session, user_id)
    await hold(session, sell_market, user_id=user_id)

    holding = await balance_of_user(session, user_id)
    proceeds = expected_total(await q_of(session, sell_market.market_id), 0, "sell", HELD)
    quantity = largest_buy_within(holding + proceeds)
    assert -expected_total(Q, 0, "buy", quantity) > holding, "the buy must need the sale"

    await _commit_in_reverse(
        monkeypatch,
        stamped_first=lambda own: buy(own, buy_market, user_id=user_id, quantity=quantity),
        stamped_second=lambda own: sell(own, sell_market, user_id=user_id, quantity=HELD),
    )

    balances = [row.balance_after for row in (await read_history(session, user_id)).rows]
    assert min(balances) >= 0, balances
