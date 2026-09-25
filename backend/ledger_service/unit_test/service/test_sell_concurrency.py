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
    errors,
    fund,
    pool_balance,
    session_factory,
)

_RETRIES = 8


async def _requoting(own: AsyncSession, attempt, *, version: int):
    """Call `attempt(version)`, and on `quote_stale` roll back and retry at
    the version the refusal names. Bounded, so a path that refuses forever is
    a failure with a name."""
    for _ in range(_RETRIES):
        try:
            return await attempt(version)
        except errors().QuoteStale as stale:
            await own.rollback()
            version = stale.current
    raise AssertionError("never got past quote_stale")


async def test_two_sells_exceeding_the_position_one_fills_one_is_refused_held(
    session: AsyncSession,
) -> None:
    """One trader holds `54.3333`, and sells `41.0000` and `29.7777` at once:
    each within the position, together over it by `16.4444`.

    **The line: the `.with_for_update()` on the `market_books` select in
    `service/trading.py::_lock_book`.** With it, the second party waits on the
    book row, is refused `quote_stale`, re-quotes, and meets the holding check
    against the position the first left behind — `insufficient_shares_held`.
    Without it, both read `state_version` 1 and a holding of `54.3333` before
    either commits, both pass both checks, and the loser's writes queue only
    on row write locks and then land a stale absolute quantity: a lost update,
    proceeds paid twice, and a position that is wrong but not negative.

    **Assertions 1–3 are the evidence.** Without the lock, 1 fails (two fills
    and no refusal — or, if the implementation writes by SQL increment, a
    `CHECK` violation, which is still not `insufficient_shares_held`), 2 fails
    (the position is `54.3333` less the *last* writer's quantity), and 3 fails
    (the balance rose by both totals). **4 and 5 are asserted because the
    criterion lists them, and they probably survive the bug:** `q` and the
    position are lost the same way, and every transaction's legs still
    balance. "Never goes negative" is deliberately not an assertion here — it
    passes on the bug.

    **Named fix, if this ever passes with the lock removed.** The barrier sits
    before the call, so about ten round trips separate it from the unlocked
    book read, and the bug shows only if both parties read the book before
    either commits. If the five-in-five run with `_lock_book`'s
    `.with_for_update()` removed passes on the bug, tighten with
    `Upstream.before_response` as a second `asyncio.Barrier(2)`, one-shot per
    party (only each party's first gate call waits, so a lone retry cannot
    hang), so both leave the gate together.
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
                return ("filled", await _requoting(own, attempt, version=1))
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

            return await _requoting(own, attempt, version=len(users))

    async with asyncio.timeout(TIMEOUT):
        results = await asyncio.gather(*(party(*p) for p in plan))

    assert len(results) == len(plan)
    await assert_q_matches_positions(session, upstream.market_id)
    assert await pool_balance(session, upstream.market_id) >= SUBSIDY
    await assert_ledger_balances(session)
