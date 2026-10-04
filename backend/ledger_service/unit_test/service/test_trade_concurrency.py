"""Two traders, or one trader twice, at the same instant. [T-2] #22, ADR 0015

Every party has its own session and transaction, and every race waits on an
`asyncio.Barrier` after connecting (ADR 0015). The two duplicate-key tests
instead run the second request inside the first's market-service call, the
one window where only the under-lock re-check stands between a retry and a
doubled `q`. Each test names what to remove to watch it go red.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.trade_fixtures import (
    Q,
    QUANTITY,
    SMALL_QUANTITY,
    TIMEOUT,
    Recorder,
    Upstream,
    assert_ledger_balances,
    assert_no_negative_user_balances,
    balance_of_user,
    book_row,
    buy,
    entities,
    entry_count,
    errors,
    expected_total,
    fund,
    pool_balance,
    position_of,
    q_of,
    retry_on_quote_stale,
    session_factory,
    trading,
    transaction_count,
    twice_affordable,
    warm,
)


def _sequential_totals(n: int, *, quantity: Decimal = QUANTITY, outcome: int = 0):
    """What `n` sequential buys cost, each priced against the `q` the one
    before left: more than `n` copies of the first cost.
    """
    q = list(Q)
    totals = []
    for _ in range(n):
        totals.append(expected_total(q, outcome, "buy", quantity))
        q[outcome] += quantity
    return totals


# =========================================================================
# One key, twice
# =========================================================================
async def test_one_key_sent_twice_moves_q_state_version_and_the_position_once(
    session: AsyncSession,
) -> None:
    """The criterion as worded: one key sent twice moves everything once, and
    both callers get the same answer.

    Without the under-lock re-check only the "same answer" assertion fails,
    and which lookup answers depends on interleaving; the test below is the
    deterministic evidence.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    start = asyncio.Barrier(2)
    factory = session_factory()

    async def attempt():
        async with factory() as own:
            await own.connection()
            await start.wait()
            return await buy(
                own, upstream, user_id=user_id, client_key="one-key", state_version=0
            )

    async with asyncio.timeout(TIMEOUT):
        first, second = await asyncio.gather(attempt(), attempt())

    assert first == second, (
        "one key, two requests, two different answers — a retry was told "
        "about a trade that is not the one it is retrying"
    )

    key = trading().trade_key(user_id, upstream.market_id, "one-key")
    assert await transaction_count(session, key=key) == 1
    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]
    assert (await book_row(session, upstream.market_id)).state_version == 1

    position = await position_of(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    )
    assert position.quantity == QUANTITY
    assert position.cost_basis == -first.total
    await assert_ledger_balances(session)


async def test_a_duplicate_arriving_after_the_pre_gate_lookup_is_replayed_under_the_lock(
    session: AsyncSession,
) -> None:
    """Evidence for the under-lock key re-check, in the one window where it is
    the only defence.

    Remove the re-check after the book `FOR UPDATE` and this fails. The
    duplicate runs inside the original's market-service call and quotes the
    version the original creates, so neither the pre-gate lookup nor the
    staleness check can rescue it. See DECISIONS.md, "A caller holding pending
    writes must establish under its own lock that the idempotency key is
    absent".
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    factory = session_factory()
    original: dict[str, object] = {}

    async def run_the_original_mid_hop() -> None:
        # One-shot, or the original's own gate call recurses into this.
        upstream.before_response = None
        async with factory() as own:
            original["result"] = await buy(
                own,
                upstream,
                user_id=user_id,
                client_key="lost-response",
                state_version=0,
            )

    upstream.before_response = run_the_original_mid_hop

    async with asyncio.timeout(TIMEOUT):
        async with factory() as duplicate_session:
            duplicate = await buy(
                duplicate_session,
                upstream,
                user_id=user_id,
                client_key="lost-response",
                state_version=1,
            )

    assert original.get("result") is not None, (
        "the original never ran, so the duplicate was not in the window this "
        "test exists to build"
    )
    assert duplicate == original["result"]

    key = trading().trade_key(user_id, upstream.market_id, "lost-response")
    assert await transaction_count(session, key=key) == 1
    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]
    assert (await book_row(session, upstream.market_id)).state_version == 1

    position = await position_of(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    )
    assert position.quantity == QUANTITY, (
        "the duplicate's position write committed alongside a replay, which "
        "is the doubled-shares bug the under-lock re-check exists to stop"
    )
    await assert_ledger_balances(session)


async def test_one_key_with_two_different_quantities_is_refused_not_replayed(
    session: AsyncSession,
) -> None:
    """The under-lock half of "A replay hit is compared against the request
    before it is returned": a different quantity is `idempotency_key_reused`.

    Remove the context comparison from the under-lock re-check and this fails;
    `posting.post`'s fingerprint is never reached.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    factory = session_factory()
    original: dict[str, object] = {}

    async def run_the_original_mid_hop() -> None:
        upstream.before_response = None
        async with factory() as own:
            original["result"] = await buy(
                own,
                upstream,
                user_id=user_id,
                client_key="reused",
                state_version=0,
                quantity=QUANTITY,
            )

    upstream.before_response = run_the_original_mid_hop

    async with asyncio.timeout(TIMEOUT):
        async with factory() as duplicate_session:
            with pytest.raises(errors().IdempotencyKeyReused):
                await buy(
                    duplicate_session,
                    upstream,
                    user_id=user_id,
                    client_key="reused",
                    state_version=1,
                    quantity=SMALL_QUANTITY,
                )
            await duplicate_session.rollback()

    assert original.get("result") is not None
    key = trading().trade_key(user_id, upstream.market_id, "reused")
    assert await transaction_count(session, key=key) == 1
    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]
    assert (await book_row(session, upstream.market_id)).state_version == 1
    await assert_ledger_balances(session)


# =========================================================================
# Overdrafts
# =========================================================================
async def test_concurrent_buys_from_one_user_across_markets_cannot_overdraw(
    session: AsyncSession,
) -> None:
    """The overdraft criterion, across distinct markets so only the trader's
    account row separates the requests (one market would queue at the book).

    Remove `.with_for_update()` from `accounts.lock` and this fails. The
    quantity is sized from the configured grant.
    """
    user_id = uuid.uuid4()
    credits = await fund(session, user_id)

    quantity = twice_affordable(credits)
    cost = -expected_total(Q, 0, "buy", quantity)
    affordable = int(credits // cost)
    assert affordable >= 1, (
        f"a trade costs {cost} against a grant of {credits}, so nothing can "
        "fill and this test asserts nothing"
    )
    parties = affordable + 1

    upstreams = [Upstream() for _ in range(parties)]
    for upstream in upstreams:
        await warm(session, upstream)

    start = asyncio.Barrier(parties)
    factory = session_factory()

    async def attempt(upstream: Upstream):
        async with factory() as own:
            await own.connection()
            await start.wait()
            try:
                await buy(
                    own,
                    upstream,
                    user_id=user_id,
                    quantity=quantity,
                    client_key=f"key-{upstream.market_id}",
                )
            except errors().InsufficientFunds:
                await own.rollback()
                return "refused"
            return "accepted"

    async with asyncio.timeout(TIMEOUT):
        outcomes = await asyncio.gather(*(attempt(u) for u in upstreams))

    assert outcomes.count("accepted") == affordable, outcomes
    assert outcomes.count("refused") == parties - affordable, outcomes

    assert await balance_of_user(session, user_id) == credits - cost * affordable
    await assert_no_negative_user_balances(session)
    await assert_ledger_balances(session)


async def test_no_user_balance_goes_negative_under_a_mixed_race(
    session: AsyncSession,
) -> None:
    """The property: whatever the interleaving of nine requests, no account
    goes negative and the ledger sums to zero."""
    users = [uuid.uuid4() for _ in range(3)]
    for user_id in users:
        credits = await fund(session, user_id)
    quantity = twice_affordable(credits)

    upstreams = [Upstream() for _ in range(3)]
    for upstream in upstreams:
        await warm(session, upstream)

    start = asyncio.Barrier(len(users) * len(upstreams))
    factory = session_factory()

    async def attempt(user_id: uuid.UUID, upstream: Upstream):
        async with factory() as own:
            await own.connection()
            await start.wait()
            try:
                await buy(
                    own,
                    upstream,
                    user_id=user_id,
                    quantity=quantity,
                    client_key=f"{user_id}-{upstream.market_id}",
                )
            except (errors().InsufficientFunds, errors().QuoteStale):
                await own.rollback()

    async with asyncio.timeout(TIMEOUT):
        await asyncio.gather(
            *(attempt(u, m) for u in users for m in upstreams)
        )

    await assert_no_negative_user_balances(session)
    await assert_ledger_balances(session)


# =========================================================================
# One market, several traders
# =========================================================================
async def test_only_one_of_many_buys_from_one_quote_fills_and_the_rest_are_stale(
    session: AsyncSession,
) -> None:
    """Six buys from one quote: exactly one fills, five are `quote_stale`.

    Remove the staleness comparison and this fails: all six fill at prices
    nobody was shown.
    """
    upstream = Upstream()
    users = [uuid.uuid4() for _ in range(6)]
    await warm(session, upstream)
    for user_id in users:
        await fund(session, user_id)

    pool_before = await pool_balance(session, upstream.market_id)

    start = asyncio.Barrier(len(users))
    factory = session_factory()

    async def attempt(user_id: uuid.UUID):
        async with factory() as own:
            await own.connection()
            await start.wait()
            try:
                return await buy(
                    own,
                    upstream,
                    user_id=user_id,
                    state_version=0,
                    client_key=f"key-{user_id}",
                )
            except errors().QuoteStale:
                await own.rollback()
                return None

    async with asyncio.timeout(TIMEOUT):
        results = await asyncio.gather(*(attempt(u) for u in users))

    filled = [r for r in results if r is not None]
    assert len(filled) == 1, f"{len(filled)} of six trades filled from one quote"

    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]
    assert (await book_row(session, upstream.market_id)).state_version == 1
    assert await pool_balance(session, upstream.market_id) == (
        pool_before + -filled[0].total
    )
    await assert_ledger_balances(session)


async def test_concurrent_traders_that_requote_each_fill_and_are_priced_in_turn(
    session: AsyncSession,
) -> None:
    """Four traders retrying on `quote_stale` all fill, each priced against
    the `q` the one before left.

    Remove `.with_for_update()` from the trade's `market_books` read and this
    fails on the pool balance: two parties would pay the same first cost.
    """
    upstream = Upstream()
    users = [uuid.uuid4() for _ in range(4)]
    await warm(session, upstream)
    for user_id in users:
        await fund(session, user_id)

    pool_before = await pool_balance(session, upstream.market_id)

    start = asyncio.Barrier(len(users))
    factory = session_factory()

    async def attempt(user_id: uuid.UUID):
        async with factory() as own:
            await own.connection()
            await start.wait()

            async def buy_at(version: int):
                return await buy(
                    own,
                    upstream,
                    user_id=user_id,
                    state_version=version,
                    client_key=f"key-{user_id}",
                )

            return await retry_on_quote_stale(
                own, buy_at, version=0, retries=len(users) * 4
            )

    async with asyncio.timeout(TIMEOUT):
        results = await asyncio.gather(*(attempt(u) for u in users))

    assert all(r is not None for r in results)

    expected = _sequential_totals(len(users))
    assert sorted(r.total for r in results) == sorted(expected), (
        "the fills were not priced one after another; two of them saw the "
        "same q and were charged the same price"
    )
    assert await q_of(session, upstream.market_id) == [
        Q[0] + QUANTITY * len(users),
        Q[1],
    ]
    assert (await book_row(session, upstream.market_id)).state_version == len(users)
    assert await pool_balance(session, upstream.market_id) == pool_before + sum(
        -total for total in expected
    )
    assert sorted(r.state_version for r in results) == [1, 2, 3, 4]
    await assert_ledger_balances(session)


# =========================================================================
# Ordering: what the lock is not held across
# =========================================================================
async def test_the_book_row_is_not_held_across_the_market_service_call(
    session: AsyncSession,
) -> None:
    """ADR 0017: the gate runs before the book lock, so a held book row cannot
    stop the hop.

    Move `ensure_trading` after the `FOR UPDATE` and this times out.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    hop_started = asyncio.Event()
    may_answer = asyncio.Event()

    async def hold_until_released() -> None:
        hop_started.set()
        await may_answer.wait()

    upstream.before_response = hold_until_released
    factory = session_factory()

    async def hold_the_book_row():
        async with factory() as holder:
            book = entities().MarketBook
            await holder.execute(
                book.__table__.select()
                .where(book.market_id == upstream.market_id)
                .with_for_update()
            )
            await hop_started.wait()
            # Observed: the trade reached the upstream although this
            # transaction has held the book row the whole time.
            await holder.rollback()
            may_answer.set()

    async def trade():
        async with factory() as own:
            return await buy(own, upstream, user_id=user_id)

    async with asyncio.timeout(TIMEOUT):
        result, _ = await asyncio.gather(trade(), hold_the_book_row())

    assert upstream.calls == 1
    assert result.state_version == 1
    await assert_ledger_balances(session)


async def test_trades_across_users_and_markets_do_not_deadlock(
    session: AsyncSession,
) -> None:
    """A smoke test, not ADR 0015 evidence: with one trade path there is no
    second lock order to deadlock against. Kept for the day a second write
    path arrives.
    """
    users = [uuid.uuid4() for _ in range(2)]
    upstreams = [Upstream() for _ in range(2)]
    for user_id in users:
        await fund(session, user_id)
    for upstream in upstreams:
        await warm(session, upstream)

    start = asyncio.Barrier(len(users) * len(upstreams))
    factory = session_factory()

    async def attempt(user_id: uuid.UUID, upstream: Upstream):
        async with factory() as own:
            await own.connection()
            await start.wait()
            try:
                await buy(
                    own,
                    upstream,
                    user_id=user_id,
                    quantity=SMALL_QUANTITY,
                    client_key=f"{user_id}-{upstream.market_id}",
                )
            except errors().QuoteStale:
                await own.rollback()

    async with asyncio.timeout(TIMEOUT):
        await asyncio.gather(*(attempt(u, m) for u in users for m in upstreams))

    await assert_ledger_balances(session)
    await assert_no_negative_user_balances(session)


async def test_a_race_that_refuses_everything_writes_nothing(
    session: AsyncSession,
) -> None:
    """The rollback claim under contention: six refused requests leave nothing
    behind."""
    upstream = Upstream()
    users = [uuid.uuid4() for _ in range(6)]
    await warm(session, upstream)
    for user_id in users:
        await fund(session, user_id)

    before = await entry_count(session)
    upstream.closes()

    start = asyncio.Barrier(len(users))
    factory = session_factory()

    async def attempt(user_id: uuid.UUID):
        async with factory() as own:
            await own.connection()
            await start.wait()
            with pytest.raises(errors().MarketClosed):
                await buy(
                    own, upstream, user_id=user_id, client_key=f"key-{user_id}"
                )
            await own.rollback()

    async with asyncio.timeout(TIMEOUT):
        await asyncio.gather(*(attempt(u) for u in users))

    assert await entry_count(session) == before
    assert await q_of(session, upstream.market_id) == list(Q)
    assert (await book_row(session, upstream.market_id)).state_version == 0
    await assert_ledger_balances(session)


async def test_a_recorder_is_not_published_to_when_every_party_is_refused(
    session: AsyncSession,
) -> None:
    """The publish is per committed trade, and a refused race commits none."""
    upstream = Upstream()
    users = [uuid.uuid4() for _ in range(4)]
    await warm(session, upstream)
    for user_id in users:
        await fund(session, user_id)
    upstream.closes()

    recorder = Recorder()
    start = asyncio.Barrier(len(users))
    factory = session_factory()

    async def attempt(user_id: uuid.UUID):
        async with factory() as own:
            await own.connection()
            await start.wait()
            with pytest.raises(errors().MarketClosed):
                await buy(
                    own,
                    upstream,
                    user_id=user_id,
                    client_key=f"key-{user_id}",
                    redis_client=recorder,
                )
            await own.rollback()

    async with asyncio.timeout(TIMEOUT):
        await asyncio.gather(*(attempt(u) for u in users))

    assert recorder.calls == []
