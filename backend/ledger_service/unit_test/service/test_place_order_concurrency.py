"""A resting placement racing a trade in another market. [T-6] #26, ADR 0015.

ADR 0024's Tests owed: "A resting placement in one market racing an instant
buy in another by the same user, with `asyncio.Barrier` and
`pg_stat_activity` lock waits, no timing. It must fail with the placement's
account lock removed."

The two share no book row, so the only lock they meet on is the user's
account row. Each party opens its own session, connects and waits on the
barrier. The trade then pauses inside `post_all`, holding the account lock,
just after it has read the user's reservation; the placement starts, and the
test waits until Postgres shows it queued on an account lock, or until it has
finished, which it can only do without the lock.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine
from core.errors import InsufficientFunds
from service import reservations
from unit_test.placement_fixtures import limit_upstream, open_at, place, reserved_credits_of
from unit_test.settlement_fixtures import PROBE_TIMEOUT, Pause, finish, reached
from unit_test.trade_fixtures import (
    assert_ledger_balances,
    balance_of_user,
    buy,
    fund,
    session_factory,
)

ZERO_Q = (Decimal("0.0000"), Decimal("0.0000"))

_ACCOUNT_LOCK_WAITERS = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() "
    "AND usename = current_user "
    "AND wait_event_type = 'Lock' "
    "AND query ILIKE '%accounts%' "
    "AND query ILIKE '%for update%' "
    "AND pid <> pg_backend_pid()"
)


async def _queued_on_an_account_or_finished(placement: asyncio.Task) -> None:
    """Return once a backend waits on an account row lock or the placement has
    finished, whichever comes first. A fresh connection per poll, because
    `pg_stat_activity` is snapshotted once per transaction."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROBE_TIMEOUT
    while not placement.done():
        async with get_engine().connect() as conn:
            if (await conn.execute(_ACCOUNT_LOCK_WAITERS)).scalar_one() == 1:
                return
        if loop.time() > deadline:
            pytest.fail("timed out: the placement neither queued nor finished")
        await asyncio.sleep(0.01)


async def test_a_resting_buy_racing_a_trade_in_another_market_cannot_both_spend(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, starting_credits: Decimal
) -> None:
    """The user's whole balance is the order's budget, so the order and the
    trade cannot both be paid for. The trade reads the reservation first and
    holds the account lock to its commit; the placement must wait for it, see
    the debit, and be refused.

    Remove `accounts.lock` from the resting placement: it reads the balance
    before the trade commits, both succeed, and the balance ends below the
    reservation.
    """
    placement_market, trade_market = limit_upstream(), limit_upstream()
    await open_at(session, placement_market, ZERO_Q)
    await open_at(session, trade_market, ZERO_Q)
    user_id = uuid.uuid4()
    await fund(session, user_id)
    # Rests: 0.4000 is below the price of 0.5000.
    limit = Decimal("0.4000")
    quantity = (starting_credits / limit).quantize(Decimal("0.0001"))

    factory = session_factory()
    start = asyncio.Barrier(2)
    trading_session, placing_session = factory(), factory()
    pause = Pause(monkeypatch, reservations, "reserved_of", session=trading_session, call=1)

    async def run_trade():
        await trading_session.connection()
        await start.wait()
        return await buy(
            trading_session,
            trade_market,
            user_id=user_id,
            quantity=Decimal("41.0000"),
            client_key="the-trade",
        )

    async def run_placement():
        await placing_session.connection()
        await start.wait()
        await pause.reached.wait()
        try:
            await place(
                placing_session,
                placement_market,
                user_id=user_id,
                limit_price=limit,
                quantity=quantity,
                client_key="the-order",
            )
        except InsufficientFunds:
            return "insufficient_funds"
        return "placed"

    try:
        trade = asyncio.create_task(run_trade())
        placement = asyncio.create_task(run_placement())
        tasks = [trade, placement]
        try:
            await reached(pause.reached, "the trade never read the reservation", tasks)
            await _queued_on_an_account_or_finished(placement)
        finally:
            traded, placed = await finish(tasks, pause.release, pause.reached)
    finally:
        await trading_session.close()
        await placing_session.close()

    assert not isinstance(traded, BaseException), traded
    assert placed == "insufficient_funds"
    balance = await balance_of_user(session, user_id)
    assert balance >= await reserved_credits_of(session, user_id)
    assert balance == starting_credits - Decimal("22.5867")
    await assert_ledger_balances(session)
