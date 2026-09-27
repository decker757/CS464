"""Two sells at once. [T-3] #23, ADR 0015

Every party gets its own session, its own connection and its own database
transaction, connects, and waits on an `asyncio.Barrier` before the call —
ADR 0015's shape, and the one `test_trade_concurrency.py`'s races use.

Every party that is refused `quote_stale` re-quotes from the error's own
`current` and retries, bounded. Two sells quoting one `state_version` are
otherwise separated by the staleness check and never reach the holding check
at all, which would make a race test on that check prove the staleness check
instead ("The holdings check is read under the book lock, and the position
takes no lock of its own", Notes).

Every holding is made by a real buy on a book opened at zero.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.sell_fixtures import (
    HELD,
    RACE_SELLS,
    assert_q_matches_positions,
    hold,
    holding,
    insufficient_shares_held,
    open_at_zero,
    sell,
)
from unit_test.trade_fixtures import (
    SUBSIDY,
    TIMEOUT,
    Upstream,
    assert_ledger_balances,
    balance_of_user,
    buy,
    fund,
    pool_balance,
    retry_on_quote_stale,
    session_factory,
)

_RETRIES = 8


async def test_two_sells_exceeding_the_position_one_fills_one_is_refused_held(
    session: AsyncSession,
) -> None:
    """One trader holds `54.3333` and sells `41.0000` and `29.7777` at once:
    each within the position, together over it by `16.4444`.

    The evidence for the `.with_for_update()` in `trading._lock_book`. Without
    it both parties read the same holding before either commits, both fill,
    and the loser's write is a lost update: assertions 1–3 fail. 4 and 5 are
    asserted because the criterion lists them, and probably survive the bug.
    "Never goes negative" is deliberately not asserted — it passes on the bug.

    If this ever passes with the lock removed, add a second, one-shot
    `asyncio.Barrier(2)` at `Upstream.before_response` so both parties leave
    the gate together.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    await fund(session, user_id)
    await hold(session, upstream, user_id=user_id)

    assert all(x <= HELD for x in RACE_SELLS) and sum(RACE_SELLS) > HELD
    before_balance = await balance_of_user(session, user_id)

    start = asyncio.Barrier(len(RACE_SELLS))
    factory = session_factory()

    async def party(quantity: Decimal):
        async with factory() as own:
            await own.connection()
            await start.wait()

            async def attempt(version: int):
                return await sell(
                    own,
                    upstream,
                    user_id=user_id,
                    quantity=quantity,
                    state_version=version,
                    client_key=f"race-{quantity}",
                )

            try:
                filled = await retry_on_quote_stale(
                    own, attempt, version=1, retries=_RETRIES
                )
                return ("filled", filled)
            except insufficient_shares_held() as refused:
                await own.rollback()
                return ("held", refused)

    async with asyncio.timeout(TIMEOUT):
        results = await asyncio.gather(*(party(x) for x in RACE_SELLS))

    # 1. Exactly one fills, and the other is refused insufficient_shares_held.
    filled = [r for kind, r in results if kind == "filled"]
    refused = [r for kind, r in results if kind == "held"]
    assert (len(filled), len(refused)) == (1, 1), results
    fill = filled[0]
    assert refused[0].held == HELD - fill.quantity
    assert refused[0].requested == sum(RACE_SELLS) - fill.quantity

    # 2. The position is the starting quantity minus the filled quantity.
    quantity, _ = await holding(session, user_id, upstream.market_id, upstream.outcomes[0])
    assert quantity == HELD - fill.quantity

    # 3. The balance rose by exactly one sell's total.
    assert await balance_of_user(session, user_id) == before_balance + fill.total

    # 4. Each outcome's q equals the sum of positions in it.
    await assert_q_matches_positions(session, upstream.market_id)

    # 5. The ledger sums to zero, in total and per transaction.
    await assert_ledger_balances(session)


async def test_q_equals_the_sum_of_positions_under_concurrent_buys_and_sells(
    session: AsyncSession,
) -> None:
    """The invariant "including concurrent ones", as a property under
    contention. **Not ADR 0015 evidence**: different users' positions are
    different rows, so if `q` is written by SQL increment it survives a
    missing book lock and this passes on the bug. The two-sells race above is
    the evidence for the lock; this holds only that a mix of concurrent buys
    and sells by several users leaves `q`, the positions, the pool and the
    ledger consistent.
    """
    upstream = Upstream()
    users = [uuid.uuid4() for _ in range(4)]
    await open_at_zero(session, upstream)
    for user_id in users:
        await fund(session, user_id)
    for i, user_id in enumerate(users):
        await hold(session, upstream, user_id=user_id, outcome=i % 2)

    plan = [
        (users[0], "sell", 0, Decimal("10.0000")),
        (users[1], "sell", 1, Decimal("29.7777")),
        (users[2], "buy", 0, Decimal("13.3333")),
        (users[3], "sell", 1, Decimal("41.0000")),
        (users[0], "buy", 1, Decimal("7.7777")),
        (users[2], "sell", 0, Decimal("17.1717")),
    ]

    start = asyncio.Barrier(len(plan))
    factory = session_factory()

    async def party(user_id: uuid.UUID, side: str, outcome: int, quantity: Decimal):
        async with factory() as own:
            await own.connection()
            await start.wait()

            async def attempt(version: int):
                return await buy(
                    own,
                    upstream,
                    user_id=user_id,
                    outcome=outcome,
                    quantity=quantity,
                    side=side,
                    state_version=version,
                    client_key=f"{user_id}-{side}-{outcome}",
                )

            return await retry_on_quote_stale(
                own, attempt, version=len(users), retries=_RETRIES
            )

    async with asyncio.timeout(TIMEOUT):
        results = await asyncio.gather(*(party(*p) for p in plan))

    assert len(results) == len(plan)
    await assert_q_matches_positions(session, upstream.market_id)
    assert await pool_balance(session, upstream.market_id) >= SUBSIDY
    await assert_ledger_balances(session)
