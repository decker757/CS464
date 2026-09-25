"""Retrying a trade. [T-2] #22, ADR 0017

A trade that committed and lost its response is retried by the client, and
everything about what happens next is ordering. ADR 0017 fixes it: the
idempotency lookup runs **first**, unlocked, and a hit is answered without a
status check and without an HTTP call. The failure that ordering prevents is
specific — a trade that executed, whose response was lost, and whose client
retried after the market closed would otherwise be told `409 market_closed`,
so a trader is told a trade failed that in fact charged them.

**The comparison this file also holds is the other half.** "A replay hit is
compared against the request before it is returned": a hit is returned only if
the request matches the stored one on `outcome_id`, `side` and `quantity`, and
a mismatch is `409 idempotency_key_reused`. Everything below drives the
**unlocked pre-gate** lookup, because that is the one a sequential retry
reaches. The under-lock re-check runs the same helper and is driven from
`test_trade_concurrency.py::test_one_key_with_two_different_quantities_is_
refused_not_replayed`, which builds the window where that one is the only
lookup a request meets.

**`side` is compared and is not exercised here.** Every retry in this file is
a buy, and the stored `context["side"]` is pinned in `test_trade.py`. The
comparison's `side` arm is exercised by [T-3] #23, in
`test_sell_replay.py::test_a_buy_key_resent_as_a_sell_is_idempotency_key_reused`.

**`state_version` is deliberately not compared**, and there is a test for
that. A client that lost its response re-previews before retrying, so the
version it now quotes is newer — and it is still the same trade. The
original's quote was valid at the instant it executed, and that instant is
over.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.trade_fixtures import (
    Q,
    QUANTITY,
    SMALL_QUANTITY,
    Recorder,
    Upstream,
    assert_ledger_balances,
    balance_of_user,
    book_row,
    buy,
    capture_sql,
    entities,
    entry_count,
    errors,
    fund,
    position_of,
    q_of,
    trading,
    transaction_count,
    warm,
    writes,
)

_KEY = "retry-me"


async def _original(session: AsyncSession, upstream: Upstream, user_id: uuid.UUID):
    """One committed trade to retry, and the upstream's counters reset so the
    assertions below are about the retry and not about this."""
    trade = await buy(
        session, upstream, user_id=user_id, client_key=_KEY, state_version=0
    )
    upstream.calls = 0
    upstream.tokens.clear()
    return trade


# =========================================================================
# A retry returns the original and writes nothing
# =========================================================================
async def test_a_retry_returns_the_original_trade(session: AsyncSession) -> None:
    """The point of the key. Identical request, identical answer.

    Equality on the whole result rather than on a field or two, because the
    criterion is that a retry "cannot return a different shape from the
    original" — which is a claim about the object, and the way it gets broken
    is a second builder that agrees on `total` and disagrees on something
    nobody thought to assert.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    original = await _original(session, upstream, user_id)

    retry = await buy(
        session, upstream, user_id=user_id, client_key=_KEY, state_version=0
    )

    assert retry == original


async def test_a_retry_executes_nothing(session: AsyncSession) -> None:
    """"Twice" means the position, `q` and `state_version` as well as the
    ledger rows.

    A retry that leaves `q` doubled against one payment satisfies the older,
    looser wording of this criterion and is the bug it was rewritten to
    prevent, so all four are asserted rather than the balance alone.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    original = await _original(session, upstream, user_id)

    entries = await entry_count(session)
    balance = await balance_of_user(session, user_id)

    await buy(session, upstream, user_id=user_id, client_key=_KEY, state_version=0)

    assert await entry_count(session) == entries
    assert await balance_of_user(session, user_id) == balance
    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]
    assert (await book_row(session, upstream.market_id)).state_version == 1

    position = await position_of(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    )
    assert position.quantity == QUANTITY
    assert position.cost_basis == -original.total
    await assert_ledger_balances(session)


async def test_a_retry_is_rebuilt_by_the_same_function_from_the_stored_row(
    session: AsyncSession,
) -> None:
    """"One function builds the response body from a `Transaction`", proved
    where it matters: the replay path returns exactly what that function makes
    of the stored transaction, so the fresh answer and every later one are the
    same object by construction rather than by two code paths agreeing."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    original = await _original(session, upstream, user_id)

    retry = await buy(
        session, upstream, user_id=user_id, client_key=_KEY, state_version=0
    )

    stored = await session.get(entities().Transaction, original.transaction_id)
    assert retry == trading().result_of(stored)


async def test_a_retry_publishes_nothing(session: AsyncSession) -> None:
    """A price event announces that a market moved. A replay moves nothing, so
    a publish here would put a duplicate on the channel for a `state_version`
    the relay has already seen — harmless only because
    `realtime_service/service/ordering.py` drops it, which is a guard that
    should stay defensive rather than become load-bearing."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    await _original(session, upstream, user_id)

    recorder = Recorder()
    await buy(
        session,
        upstream,
        user_id=user_id,
        client_key=_KEY,
        state_version=0,
        redis_client=recorder,
    )

    assert recorder.calls == []


# =========================================================================
# The replay runs before the gate
# =========================================================================
async def test_a_retry_makes_no_call_to_market_service(
    session: AsyncSession,
) -> None:
    """"Replayed and returned **before** any status check and before any HTTP
    call is made."

    Counted rather than inferred. A replay that made the hop anyway would pass
    every other test in this file and would put one round trip per retry on
    market_service, on the path a client hits precisely when something has
    already gone wrong for it.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    await _original(session, upstream, user_id)

    await buy(session, upstream, user_id=user_id, client_key=_KEY, state_version=0)

    assert upstream.calls == 0


async def test_a_retry_after_the_market_closed_still_returns_the_trade(
    session: AsyncSession,
) -> None:
    """The failure ADR 0017 put the lookup first to prevent.

    The trade committed while the market was open. The response was lost. The
    market closed. The client retries. Answering `409 market_closed` here
    tells a trader their trade failed when it had charged them — and the trade
    already happened, so the only question a retry asks is what its answer
    was. A market closing afterwards does not change that answer.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    original = await _original(session, upstream, user_id)

    upstream.closes()

    retry = await buy(
        session, upstream, user_id=user_id, client_key=_KEY, state_version=0
    )

    assert retry == original
    assert upstream.calls == 0


async def test_the_three_cases_in_order_on_one_market(
    session: AsyncSession,
) -> None:
    """The criterion's own test: "replay-after-close returns the original, a
    fresh trade after close returns 409, and a fresh trade on an open market
    executes."

    Driven as one sequence on one market rather than as three tests, because
    the criterion is about the three *together* — a gate that ran first would
    pass the third and fail the first, and a lookup that swallowed the gate
    entirely would pass the first and fail the second.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    original = await _original(session, upstream, user_id)

    upstream.closes()

    replayed = await buy(
        session, upstream, user_id=user_id, client_key=_KEY, state_version=0
    )
    assert replayed == original

    with pytest.raises(errors().MarketClosed):
        await buy(
            session, upstream, user_id=user_id, client_key="fresh", state_version=1
        )
    await session.rollback()

    upstream.body["status"] = "open"
    fresh = await buy(
        session, upstream, user_id=user_id, client_key="fresh", state_version=1
    )
    assert fresh.transaction_id != original.transaction_id
    assert (await book_row(session, upstream.market_id)).state_version == 2
    await assert_ledger_balances(session)


async def test_the_replay_lookup_takes_no_lock_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """"The replay lookup is unlocked, and that is safe because it can only
    ever short-circuit."

    A hit names a committed, append-only transaction, so no lock would make
    the answer better; a miss is trusted for nothing except declining to skip
    the gate. Asserted structurally — a replayed request sends no `FOR UPDATE`
    and no write to Postgres at all, so the book row is never even touched.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    await _original(session, upstream, user_id)

    with capture_sql() as statements:
        await buy(
            session, upstream, user_id=user_id, client_key=_KEY, state_version=0
        )

    assert not [s for s in statements if "for update" in s.lower()], statements
    assert not writes(statements), writes(statements)


# =========================================================================
# A reused key naming a different trade
# =========================================================================
async def test_a_retry_with_a_different_quantity_is_refused(
    session: AsyncSession,
) -> None:
    """The pre-gate half of the comparison rule.

    **Remove the context comparison from the replay helper and this fails
    silently**: the client asked to buy 13.3333 shares, and is answered `201`
    with a body saying it bought 41 and a transaction id that proves it.

    `posting.post`'s fingerprint is not a substitute and this is the test that
    shows why — it is never reached, because the pre-gate lookup returns
    first.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    await _original(session, upstream, user_id)
    entries = await entry_count(session)

    with pytest.raises(errors().IdempotencyKeyReused):
        await buy(
            session,
            upstream,
            user_id=user_id,
            client_key=_KEY,
            state_version=0,
            quantity=SMALL_QUANTITY,
        )
    await session.rollback()

    key = trading().trade_key(user_id, upstream.market_id, _KEY)
    assert await transaction_count(session, key=key) == 1
    assert await entry_count(session) == entries
    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]


async def test_a_retry_naming_the_other_outcome_is_refused(
    session: AsyncSession,
) -> None:
    """The case the fingerprint structurally cannot catch, which is why the
    comparison reads `context` rather than leaning on `post`.

    `posting._fingerprint` hashes the kind and `account_id:amount` per leg.
    Both accounts here are fixed by `(user_id, market_id)`, which are already
    inside the derived key, so the hash sees nothing but the total — and a
    trade on the other outcome of a binary market at a total that quantizes
    the same way hashes identically. Without this check the trader is handed a
    position in one outcome as proof that their trade on the other succeeded.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    await _original(session, upstream, user_id)

    with pytest.raises(errors().IdempotencyKeyReused):
        await buy(
            session,
            upstream,
            user_id=user_id,
            client_key=_KEY,
            state_version=0,
            outcome=1,
        )
    await session.rollback()

    assert (
        await position_of(
            session, user_id, upstream.market_id, upstream.outcomes[1]
        )
        is None
    )


async def test_the_same_quantity_spelled_differently_is_still_a_replay(
    session: AsyncSession,
) -> None:
    """The comparison is numeric, and this is why it has to be.

    "A quantity takes money's scale of 4 at the API boundary" echoes the
    quantity back exactly as it arrived, trailing zeros and all, and `context`
    stores what was sent. A string comparison would refuse a client that
    retried `41` after sending `41.0000` — the same trade, typed twice — with
    an error telling it to generate a new key, which is advice that would make
    it trade twice.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    original = await _original(session, upstream, user_id)

    assert QUANTITY == Decimal("41") and str(QUANTITY) != "41", (
        "this test needs two spellings of one number to mean anything"
    )

    retry = await buy(
        session,
        upstream,
        user_id=user_id,
        client_key=_KEY,
        state_version=0,
        quantity=Decimal("41"),
    )

    assert retry == original


async def test_a_retry_quoting_a_newer_state_version_is_still_a_replay(
    session: AsyncSession,
) -> None:
    """`state_version` is not part of the comparison, and this is the case
    that decides it.

    A client that lost its response re-previews before retrying, and the
    market has moved in the meantime — by its own trade, at least. The version
    it quotes is therefore newer than the one it originally sent, and the
    trade is still the same trade. Comparing it would refuse exactly the retry
    the whole replay-first ordering exists to serve.

    It cannot double as a staleness check either: staleness is decided under
    the book lock against a trade that is about to execute, and a replay
    executes nothing.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    original = await _original(session, upstream, user_id)

    current = (await book_row(session, upstream.market_id)).state_version
    assert current == 1, "the original's own trade should have moved the book"

    retry = await buy(
        session, upstream, user_id=user_id, client_key=_KEY, state_version=current
    )

    assert retry == original
    assert retry.state_version == original.state_version
