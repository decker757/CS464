"""Two traders, or one trader twice, at the same instant. [T-2] #22, ADR 0015

Nothing here is simulated. Every party gets its own session, its own
connection and its own database transaction, because the thing under test is
what Postgres does when two transactions want the same rows.

**Every race is gated on an `asyncio.Barrier`, and that is not decoration.**
`asyncio.gather` alone starts coroutines in order and the first can be most of
the way through its work before the second has opened a connection, which
produces a test that passes because the two never overlapped. `await
own.connection()` forces the connection to exist, then the barrier holds both
there until both have arrived. ADR 0015 makes that the standard for any test
in this repository asserting something about two transactions, and #22's
criteria restate it.

**Two of these are not barrier races, and that is deliberate.** A barrier
decides only that both parties start together; it does not decide who wins,
and for the duplicate-key tests the interleaving *is* the claim. Those two use
the upstream's `before_response` hook instead: the second request's whole
trade runs from inside the first's market-service call, which puts it exactly
in the window ADR 0017 describes — the unlocked pre-gate lookup has already
missed, and the key exists by the time the book row is taken. That window is
the only one in which the under-lock re-check is the thing standing between a
retry and a doubled `q`, and a barrier cannot be relied on to produce it.

**Each test names what to remove to watch it go red**, and where the
counterfactual is a *move* rather than a deletion, or where the test is a
smoke test rather than evidence, the docstring says so rather than letting the
name imply more than the test holds.
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
    session_factory,
    trading,
    transaction_count,
    twice_affordable,
    warm,
)


def _sequential_totals(n: int, *, quantity: Decimal = QUANTITY, outcome: int = 0):
    """What `n` buys of `quantity` cost when each is priced against the `q`
    the one before it left.

    This is the number a market with a working book lock produces, and it is
    strictly larger than `n` copies of the first cost, because LMSR prices
    each successive share higher. That gap is the whole evidence in
    `test_concurrent_traders_that_requote_each_fill_and_are_priced_in_turn`.
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
    """The criterion, written the way the criterion words it.

    Two requests, one derived key, one quoted `state_version`, released
    together. Exactly one transaction, one debit, one increment of `q`, one
    increment of `state_version`, and one position — and both callers get the
    same answer, because a retry asks what its answer was and is entitled to
    be told.

    **This is the criterion, and `test_a_duplicate_arriving_after_the_pre_gate
    _lookup_is_replayed_under_the_lock` below is the evidence.** Read the two
    together. The criterion says to run this with the under-lock re-check
    removed and watch it fail, and it does fail — but not on the assertions
    the criterion names. With the re-check gone the loser wakes from the book
    lock, reads a `state_version` the winner has just bumped, and is refused
    `quote_stale` before it reaches `post`; so `q`, `state_version` and the
    position have each still moved exactly once and only the "both callers
    succeeded, with the same body" assertion goes red. Which lookup answers
    the loser here — the unlocked pre-gate one or the one under the lock —
    also depends on how the two interleave, which is why the deterministic
    test below exists rather than this one carrying the whole claim.
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
    """The evidence for the under-lock re-check, in the one window where it is
    the only defence.

    **Remove the `find_by_idempotency_key` re-check issued after the
    `SELECT ... FROM market_books ... FOR UPDATE` and before the first write,
    and this fails.** Nothing else in the suite covers it: the unlocked
    pre-gate lookup catches every sequential retry, and the staleness check
    catches most concurrent ones.

    The window is built rather than waited for, because waiting for it is
    flaky. The duplicate's whole trade runs from inside the original's
    market-service call, which is ADR 0017's own ordering played out: the
    duplicate's pre-gate lookup has already missed, the original commits while
    the duplicate is mid-hop, and by the time the duplicate takes the book row
    the key exists. The duplicate quotes `1` — the version the original
    creates — so the staleness check cannot rescue it either, which is what
    leaves the re-check alone.

    With the re-check gone this is the corruption "A caller holding pending
    writes must establish under its own lock that the idempotency key is
    absent" measured: the duplicate writes `q`, `state_version` and the
    position into its session, calls `post`, and `post`'s replay branch
    commits all of it against a payment that was made once. With the re-check
    gone *and* `posting.post`'s guard in place the same removal surfaces as a
    `PendingWritesOnReplay` instead — a refused request rather than a doubled
    position, which is exactly the trade that guard exists to make.
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
    before it is returned".

    Same window as the test above, same reason it is built rather than waited
    for — but the duplicate asks for a *different* quantity. It must not be
    handed the original's trade with a `201`. One of the two executes and the
    other is refused `idempotency_key_reused`.

    **Remove the context comparison from the under-lock re-check and this
    fails**, silently: the duplicate is answered with the original's body, so
    a trader who asked for 13.3333 shares is told they bought 41 and shown a
    transaction id that proves it. `posting.post`'s fingerprint cannot save
    it — it is never reached, and it hashes only the kind and the legs, whose
    accounts are already fixed by the derived key.

    The pre-gate half of the same rule is
    `test_trade_replay.py::test_a_retry_with_a_different_quantity_is_refused`.
    Both go through one helper; these are the two callers of it.
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
    """The overdraft criterion, in the only shape that is evidence.

    **The markets are distinct, and that is the whole design of this test.**
    Two concurrent buys by one trader in *one* market are serialised by the
    book row lock before they ever reach an account, so `accounts.lock` could
    be deleted outright and a same-market version of this test would still
    pass — it would be proving the book lock twice and the account lock never.
    Distinct markets mean distinct book rows, nothing queues at the book, and
    the trader's own USER row is the only thing between the requests.

    **Remove `.with_for_update()` from `service/accounts.py::lock` and this
    fails**: every party reads the same balance, every party decides there is
    enough, and the account ends below zero. That is the line, and it is
    Ernest's — this ticket is the first caller that can reach it from two
    directions at once.

    The quantity is sized from the configured grant rather than written
    down (`twice_affordable`), so the test neither goes vacuous nor asks its
    barrier for more connections than the pool holds for whoever sets
    `STARTING_CREDITS` in their own `.env`.
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
    """The property rather than the arithmetic of one scenario.

    Three traders against three markets, nine requests, every one of them for
    a quantity each trader can afford twice and not three times. Some fill, some are refused for
    funds, some are refused stale. Whatever the interleaving, no account holds
    less than nothing and the ledger still sums to zero.
    """
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
    """What strict staleness actually does under contention, stated outright
    rather than discovered by [FE] #49.

    Six traders all quote `state_version = 0` and all press buy together.
    Exactly one fills. The other five are refused `quote_stale`, because the
    first fill made their quote describe a market that no longer exists —
    which is the protection, not a defect. `q` moves by one fill, the version
    rises by one, and the pool collects exactly one cost.

    **Remove the staleness comparison and this fails**: all six fill, `q`
    moves six times, and five traders are charged against a price they were
    never shown. That is the line, and this is the test that holds it under
    contention rather than against a hand-written version number.
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
    """Four traders, one market, every one of them filling — and each priced
    against the `q` the one before it left.

    Each party retries on `quote_stale`, taking the new version out of the
    error's own `details`, which is what a client does and what those two
    fields are for. That turns six simultaneous requests into four sequential
    fills without anybody coordinating.

    **Remove `.with_for_update()` from the `market_books` read in the trade
    path and this fails.** The assertion that catches it is the pool balance,
    not `q`: if `q` and `state_version` are written as SQL increments they
    survive a missing lock intact, and "final `q` is the sum of the fills"
    stays green. What cannot survive is the price — without the lock two
    parties read the same `q`, both pass staleness against the same version,
    and both pay the *first* trade's cost. The pool then holds less than the
    four sequential costs, and `state_version` ends below four.
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
            version = 0
            # Bounded, so a path that refuses forever is a failure with a
            # name rather than a suite that spins.
            for _ in range(len(users) * 4):
                try:
                    return await buy(
                        own,
                        upstream,
                        user_id=user_id,
                        state_version=version,
                        client_key=f"key-{user_id}",
                    )
                except errors().QuoteStale as stale:
                    await own.rollback()
                    version = stale.current
            raise AssertionError(f"{user_id} never filled")

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
    """ADR 0017's "the check is issued before the book lock", as a property
    somebody can observe rather than as a line in a docstring.

    Another session holds the book row `FOR UPDATE` and does not let go. A
    trade starts anyway and must still reach market_service: the hop precedes
    the lock, so a held book row cannot stop it. Once the hop is observed the
    holder releases and the trade finishes.

    **Move `ensure_trading` to after the `SELECT ... FOR UPDATE` and this
    fails** — the trade blocks on the row before it ever dials, the hop never
    happens, and `asyncio.timeout` turns what would otherwise be a hung suite
    into a named failure. The cost that ordering avoids is exact: every trade
    in a market serialised behind a remote round trip, and a hung dependency
    holding the hottest row in the system for the whole terms timeout per
    queued trade.
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
    """An ordering smoke test, not ADR 0015 evidence, and the name of the
    thing it cannot do is worth writing down.

    D-015's "book row before account rows" becomes a live rule in this ticket
    because `post` now runs *inside* the book lock, so both are held at once.
    But there is only one trade path, so there is no second ordering for it to
    deadlock against, and the counterfactual is a *move* — calling `post`
    before taking the book row — rather than a line to delete. A deadlock also
    surfaces as a hang rather than as an assertion, so this is bounded and
    proves only that four crossing trades complete.

    Kept because the shape is the one that would deadlock the day a second
    write path arrives: two markets, two traders, every party contending on a
    book row and an account row at the same time.
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
    """The rollback claim under contention rather than in isolation.

    Six requests against a closed market, released together. Not one of them
    may leave an entry, a `q`, a version bump or a position behind — the shape
    where a path that writes first and checks afterwards finally shows itself.
    """
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
    """The publish is per committed trade, and a refused race commits none.

    Kept in this file rather than in `test_trade_publish.py` because the
    claim is about contention: a publish fired from a path that had not yet
    decided whether it was going to commit would show up here and nowhere
    else.
    """
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
