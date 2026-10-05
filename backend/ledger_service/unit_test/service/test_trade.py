"""Buying shares. [T-2] #22

The arithmetic, the rows written and the rows not, driven through
`service/trading.py` with the outbound call stubbed at the transport. Routes
are `test_trade_routes.py`'s; races `test_trade_concurrency.py`'s; retries
`test_trade_replay.py`'s; the publish `test_trade_publish.py`'s.

Money is compared with `==` on exact Decimals, never approximately: a
tolerance would pass a buy that floored, or a total that went through a float.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import idle_in_transaction, terms_client_over
from unit_test.trade_fixtures import (
    B,
    Q,
    QUANTITY,
    SMALL_QUANTITY,
    ZERO,
    Recorder,
    Upstream,
    assert_ledger_balances,
    assert_rounding_is_load_bearing,
    balance_of_user,
    book_row,
    buy,
    capture_sql,
    entities,
    entry_count,
    errors,
    expected_total,
    floored_total,
    fund,
    grants,
    pool_balance,
    position_of,
    preview,
    pricing,
    q_of,
    session_factory,
    token,
    trading,
    transaction_count,
    unaffordable,
    warm,
    writes,
)


async def _committed_state(fn):
    """Run an assertion from a session of its own, which under READ COMMITTED
    sees only what landed, not the caller's rolled-back writes."""
    async with session_factory()() as other:
        return await fn(other)


# =========================================================================
# The constants these tests are built on
# =========================================================================
def test_the_rounding_direction_is_observable_in_these_fixtures() -> None:
    """A guard on the fixtures, not the trade path: on a cost exact at scale 4
    the ceiling and floor agree and every rounding test below proves nothing.
    """
    assert_rounding_is_load_bearing(Q, 0, QUANTITY)
    assert_rounding_is_load_bearing(Q, 0, SMALL_QUANTITY)
    assert_rounding_is_load_bearing(Q, 1, QUANTITY)


# =========================================================================
# The cost, and its agreement with the preview
# =========================================================================
async def test_a_buy_debits_exactly_the_total_the_preview_quoted(
    session: AsyncSession,
) -> None:
    """The first criterion: the trade charges exactly the quote the preview
    returned against the same state (D-014)."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    quote = await preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[0],
        side=pricing().Side.BUY,
        quantity=QUANTITY,
        access_token=token(),
        terms_client=terms_client_over(upstream.transport),
    )

    before = await balance_of_user(session, user_id)
    trade = await buy(
        session, upstream, user_id=user_id, state_version=quote.state_version
    )

    assert trade.total == quote.total
    assert await balance_of_user(session, user_id) == before + quote.total


async def test_the_cost_rounds_ceiling_and_not_floor(session: AsyncSession) -> None:
    """"A buy rounds ceiling", asserted as a direction: equal to the ceiling
    and not the floor (D-039)."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    assert trade.total == expected_total(Q)
    assert trade.total != floored_total(Q)
    assert trade.total < floored_total(Q), (
        "a buy's total is negative, so charging more means a smaller number; "
        "this is the assertion that catches ROUND_FLOOR wearing a minus sign"
    )


async def test_the_total_is_stored_at_the_columns_scale(
    session: AsyncSession,
) -> None:
    """Four decimal places exactly, what `Numeric(18, 4)` holds and the
    fingerprint hashes."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    assert trade.total.as_tuple().exponent == -4


# =========================================================================
# The ledger rows
# =========================================================================
async def test_the_legs_debit_the_trader_and_credit_the_market_pool(
    session: AsyncSession,
) -> None:
    """The second criterion's "debit user, credit market pool", as two rows
    that sum to zero rather than as two balances that happen to have moved."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    pool_before = await pool_balance(session, upstream.market_id)
    user_before = await balance_of_user(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)
    magnitude = -trade.total

    assert await balance_of_user(session, user_id) == user_before - magnitude
    assert await pool_balance(session, upstream.market_id) == pool_before + magnitude


async def test_a_trade_writes_exactly_two_entries(session: AsyncSession) -> None:
    """"No balance mutated without matching ledger rows", from the other side:
    one movement, two legs, and nothing else appended."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    before = await entry_count(session)
    await buy(session, upstream, user_id=user_id)

    assert await entry_count(session) == before + 2


async def test_the_transaction_kind_is_trade_buy(session: AsyncSession) -> None:
    """A kind of its own, so the history can say what this was."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    transaction = await session.get(entities().Transaction, trade.transaction_id)
    assert transaction is not None
    assert transaction.kind is entities().TransactionKind.TRADE_BUY


async def test_the_ledger_still_sums_to_zero_after_a_trade(
    session: AsyncSession,
) -> None:
    """The invariant, on the happy path, so the races later have a baseline."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    await buy(session, upstream, user_id=user_id)

    await assert_ledger_balances(session)


# =========================================================================
# The book
# =========================================================================
async def test_q_moves_by_the_filled_quantity_on_the_traded_outcome_only(
    session: AsyncSession,
) -> None:
    """A trade moves one outcome's `q` and leaves the rest alone, which an
    asymmetric `q` makes visible."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    await buy(session, upstream, user_id=user_id, outcome=0)

    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]


async def test_the_state_version_rises_by_exactly_one(
    session: AsyncSession,
) -> None:
    """D-011's quote reference, incremented once per trade; the response
    reports the post-trade value."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    before = (await book_row(session, upstream.market_id)).state_version
    trade = await buy(session, upstream, user_id=user_id, state_version=before)

    after = (await book_row(session, upstream.market_id)).state_version
    assert after == before + 1
    assert trade.state_version == after


async def test_state_changed_at_advances_and_the_ledger_stamp_is_no_earlier(
    session: AsyncSession,
) -> None:
    """The fourth write, served by the snapshot (D-029). The trade's own time,
    taken under the book lock; its ledger entries are stamped under the
    account locks, which can only be the same moment or later (#187)."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    before = (await book_row(session, upstream.market_id)).state_changed_at
    trade = await buy(session, upstream, user_id=user_id)

    book = await book_row(session, upstream.market_id)
    transaction = await session.get(entities().Transaction, trade.transaction_id)
    assert book.state_changed_at > before
    assert transaction.occurred_at >= book.state_changed_at


# =========================================================================
# The position
# =========================================================================
async def test_a_buy_writes_the_position_with_its_quantity_and_cost_basis(
    session: AsyncSession,
) -> None:
    """The shares bought and the credits paid: `cost_basis` is the positive
    magnitude, not the signed total (D-017)."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    position = await position_of(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    )
    assert position is not None
    assert position.quantity == QUANTITY
    assert position.cost_basis == -trade.total


async def test_a_second_buy_accumulates_on_the_same_row(
    session: AsyncSession,
) -> None:
    """A second buy adds to the same row. Its cost differs from the first, so
    a doubled or overwritten `cost_basis` would fail.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    first = await buy(session, upstream, user_id=user_id, client_key="one")
    second = await buy(
        session, upstream, user_id=user_id, client_key="two", state_version=1
    )

    assert first.total != second.total, (
        "the second buy must be priced against the q the first one left, or "
        "this test cannot tell an accumulating cost_basis from a doubled one"
    )

    position = await position_of(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    )
    assert position.quantity == QUANTITY + QUANTITY
    assert position.cost_basis == -first.total + -second.total


async def test_two_outcomes_of_one_market_are_two_positions(
    session: AsyncSession,
) -> None:
    """`outcome_id` is part of the key, so both sides are two rows."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    await buy(session, upstream, user_id=user_id, outcome=0, client_key="a")
    await buy(
        session,
        upstream,
        user_id=user_id,
        outcome=1,
        client_key="b",
        quantity=SMALL_QUANTITY,
        state_version=1,
    )

    # Read before the second lookup, which expires the session: a later read
    # would be a lazy load and `MissingGreenlet`.
    first = await position_of(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    )
    first_quantity = first.quantity
    second = await position_of(
        session, user_id, upstream.market_id, upstream.outcomes[1]
    )
    assert first_quantity == QUANTITY
    assert second.quantity == SMALL_QUANTITY


async def test_the_position_is_stored_at_money_scale(
    session: AsyncSession,
) -> None:
    """"Shares keep money's scale of 4, in `q` and in positions"."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    await buy(session, upstream, user_id=user_id, quantity=SMALL_QUANTITY)

    position = await position_of(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    )
    assert position.quantity == SMALL_QUANTITY
    assert position.quantity.as_tuple().exponent == -4
    assert position.cost_basis.as_tuple().exponent == -4


# =========================================================================
# The idempotency key
# =========================================================================
async def test_the_stored_key_is_derived_by_the_server(
    session: AsyncSession,
) -> None:
    """`trade:<user_id>:<market_id>:<client key>`; the client's string is never
    the stored key, or a trader could squat another user's grant key.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    await buy(session, upstream, user_id=user_id, client_key="abc-123")

    derived = trading().trade_key(user_id, upstream.market_id, "abc-123")
    assert derived == f"trade:{user_id}:{upstream.market_id}:abc-123"
    assert await transaction_count(session, key=derived) == 1
    assert await transaction_count(session, key="abc-123") == 0


async def test_a_client_key_naming_the_grant_namespace_cannot_touch_it(
    session: AsyncSession,
) -> None:
    """A client key spelled like another user's grant key leaves that grant
    untouched."""
    upstream = Upstream()
    attacker = uuid.uuid4()
    victim = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, attacker)

    await buy(
        session,
        upstream,
        user_id=attacker,
        client_key=grants().grant_key(victim),
    )

    assert await transaction_count(session, key=grants().grant_key(victim)) == 0

    await grants().ensure_granted(session, victim)
    await session.commit()
    assert await balance_of_user(session, victim) > ZERO


async def test_two_users_sending_one_client_key_make_two_trades(
    session: AsyncSession,
) -> None:
    """The `user_id` component: one client key from two users is two trades."""
    upstream = Upstream()
    first_user = uuid.uuid4()
    second_user = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, first_user)
    await fund(session, second_user)

    first = await buy(
        session, upstream, user_id=first_user, client_key="same", state_version=0
    )
    second = await buy(
        session, upstream, user_id=second_user, client_key="same", state_version=1
    )

    assert first.transaction_id != second.transaction_id
    assert first.total != second.total
    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY * 2, Q[1]]


# =========================================================================
# The response
# =========================================================================
async def test_the_context_carries_everything_the_response_is_rebuilt_from(
    session: AsyncSession,
) -> None:
    """`context` is the only durable description of a trade, so a replay can
    rebuild the response only from what it holds."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    transaction = await session.get(entities().Transaction, trade.transaction_id)
    context = transaction.context
    assert set(context) >= {
        "user_id",
        "market_id",
        "outcome_id",
        "side",
        "quantity",
        "total",
        "state_version",
    }
    assert context["user_id"] == str(user_id)
    assert context["market_id"] == str(upstream.market_id)
    assert context["outcome_id"] == str(upstream.outcomes[0])
    assert context["side"] == "buy"
    assert Decimal(str(context["quantity"])) == QUANTITY
    assert Decimal(str(context["total"])) == trade.total
    assert int(context["state_version"]) == trade.state_version


async def test_the_response_is_what_the_one_builder_makes_of_the_transaction(
    session: AsyncSession,
) -> None:
    """One function builds the response from a `Transaction`, and the fresh
    path uses it too, so a replay cannot disagree."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    transaction = await session.get(entities().Transaction, trade.transaction_id)
    assert trading().result_of(transaction) == trade


async def test_the_response_carries_no_prices(session: AsyncSession) -> None:
    """A replayed price would be stale, unlike `total`. Checked over attribute
    names, so any price field added later fails.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    named = {name for name in dir(trade) if not name.startswith("_")}
    assert not {n for n in named if "price" in n.lower()}, named


# =========================================================================
# The order: what commits, and what runs under the lock
# =========================================================================
async def test_the_book_row_is_locked_before_any_account_row(
    session: AsyncSession,
) -> None:
    """D-015: book row before account rows, since `post` runs inside the book
    lock. The first `FOR UPDATE` names `market_books`.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    with capture_sql() as statements:
        await buy(session, upstream, user_id=user_id)

    taken = [s.lower() for s in statements if "for update" in s.lower()]
    assert taken, "the trade took no row lock at all"
    assert "market_books" in taken[0], (
        f"the first lock this trade took was not the book row:\n{taken[0]}"
    )
    assert any("accounts" in s for s in taken[1:]), (
        "the account locks `posting.post` takes are missing, so `post` did "
        "not run under the book lock"
    )


async def test_a_cold_market_opens_its_book_before_the_trade(
    session: AsyncSession,
) -> None:
    """A market nobody has touched still trades. `ensure_open` commits, so it
    runs before the book lock."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    book = await book_row(session, upstream.market_id)
    assert book.liquidity_b == B
    assert trade.state_version == 1
    assert await q_of(session, upstream.market_id) == [QUANTITY, ZERO]
    await assert_ledger_balances(session)


async def test_a_user_who_has_never_been_read_is_granted_first(
    session: AsyncSession,
) -> None:
    """A brand-new trader is granted before the lock, or their first trade is
    refused against an empty account."""
    from core.config import get_settings  # noqa: PLC0415

    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)

    trade = await buy(session, upstream, user_id=user_id)

    assert await transaction_count(session, key=grants().grant_key(user_id)) == 1
    # `total` is negative on a buy, so the grant and the charge add.
    assert await balance_of_user(session, user_id) == (
        get_settings().starting_credits + trade.total
    )
    await assert_ledger_balances(session)


async def test_the_market_is_asked_once_per_trade_with_the_callers_own_token(
    session: AsyncSession,
) -> None:
    """ADR 0017's hop: one call per trade, with the caller's own token."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    mine = token()

    await buy(session, upstream, user_id=user_id, access_token=mine)

    assert upstream.calls == 1
    assert upstream.tokens == [mine]


# =========================================================================
# Refusals: each one writes nothing
# =========================================================================
async def test_a_closed_market_is_refused_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """ADR 0017's `409 market_closed`, on a market closed after its book was
    opened, so the gate must be a live read."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    before = await entry_count(session)

    upstream.closes()

    with pytest.raises(errors().MarketClosed):
        await buy(session, upstream, user_id=user_id)
    await session.rollback()

    assert await _committed_state(entry_count) == before
    assert await _committed_state(
        lambda s: q_of(s, upstream.market_id)
    ) == list(Q)
    assert (
        await _committed_state(lambda s: book_row(s, upstream.market_id))
    ).state_version == 0


async def test_an_insufficient_balance_is_refused_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """`insufficient_funds` (409), under the account lock. Also the rollback
    test for "all four writes or none": the pending book writes must not land.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    credits = await fund(session, user_id)

    quantity = unaffordable(credits)
    assert -expected_total(Q, 0, "buy", quantity) > credits, (
        "this quantity has to cost more than the starting grant or the test "
        "is not exercising a refusal at all"
    )
    before = await entry_count(session)

    with pytest.raises(errors().InsufficientFunds):
        await buy(session, upstream, user_id=user_id, quantity=quantity)
    await session.rollback()

    assert await _committed_state(entry_count) == before
    assert await _committed_state(lambda s: q_of(s, upstream.market_id)) == list(Q)
    assert (
        await _committed_state(lambda s: book_row(s, upstream.market_id))
    ).state_version == 0
    assert (
        await _committed_state(
            lambda s: position_of(s, user_id, upstream.market_id, upstream.outcomes[0])
        )
        is None
    )
    assert await _committed_state(lambda s: balance_of_user(s, user_id)) == credits


async def test_an_unknown_outcome_is_refused_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """`unknown_outcome` (422): the market was found and the parameter is
    wrong."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    before = await entry_count(session)

    with pytest.raises(errors().UnknownOutcome):
        await trading().execute(
            session,
            upstream.market_id,
            user_id=user_id,
            outcome_id=uuid.uuid4(),
            side=pricing().Side.BUY,
            quantity=QUANTITY,
            state_version=0,
            idempotency_key="k",
            access_token=token(),
            redis_client=Recorder(),
            terms_client=terms_client_over(upstream.transport),
        )
    await session.rollback()

    assert await _committed_state(entry_count) == before
    assert await _committed_state(lambda s: q_of(s, upstream.market_id)) == list(Q)


async def test_a_quantity_that_prices_above_the_column_is_refused(
    session: AsyncSession,
) -> None:
    """`quantity_too_large` (422), D-040's bound on the cost."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    before = await entry_count(session)

    with pytest.raises(errors().QuantityTooLarge):
        await buy(
            session,
            upstream,
            user_id=user_id,
            quantity=Decimal("1000000000000000.0000"),
        )
    await session.rollback()

    assert await _committed_state(entry_count) == before


async def test_an_unreachable_market_service_is_refused_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """`market_terms_unavailable` (503): the gate fails closed (ADR 0017)."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    before = await entry_count(session)

    with pytest.raises(errors().MarketTermsUnavailable):
        await buy(session, upstream, user_id=user_id, transport=upstream.dead)
    await session.rollback()

    assert await _committed_state(entry_count) == before
    assert await _committed_state(lambda s: q_of(s, upstream.market_id)) == list(Q)


async def test_a_market_nobody_has_heard_of_is_not_found(
    session: AsyncSession,
) -> None:
    """404 on a cold market the upstream does not know; `test_market_status.py`
    owns the 503 case."""
    upstream = Upstream(status_code=404)
    user_id = uuid.uuid4()
    await fund(session, user_id)

    with pytest.raises(errors().MarketNotFound):
        await buy(session, upstream, user_id=user_id)
    await session.rollback()


# =========================================================================
# Staleness
# =========================================================================
async def test_a_quote_older_than_the_book_is_refused_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """Strict equality on `state_version`, the trade's only staleness check,
    after one real trade has moved the book."""
    upstream = Upstream()
    mover = uuid.uuid4()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, mover)
    await fund(session, user_id)

    await buy(session, upstream, user_id=mover, client_key="mover")
    before = await entry_count(session)
    q_before = await q_of(session, upstream.market_id)

    with pytest.raises(errors().QuoteStale):
        await buy(session, upstream, user_id=user_id, state_version=0)
    await session.rollback()

    assert await _committed_state(entry_count) == before
    assert await _committed_state(lambda s: q_of(s, upstream.market_id)) == q_before


async def test_a_quote_newer_than_the_book_is_refused_too(
    session: AsyncSession,
) -> None:
    """Strict equality means both directions: a `>=` would accept a version
    that does not exist yet."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    with pytest.raises(errors().QuoteStale):
        await buy(session, upstream, user_id=user_id, state_version=7)
    await session.rollback()


async def test_quote_stale_carries_the_quoted_and_the_current_version(
    session: AsyncSession,
) -> None:
    """`quoted` and `current` travel with the error, so a client can
    re-preview and retry."""
    upstream = Upstream()
    mover = uuid.uuid4()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, mover)
    await fund(session, user_id)
    await buy(session, upstream, user_id=mover, client_key="mover")

    with pytest.raises(errors().QuoteStale) as raised:
        await buy(session, upstream, user_id=user_id, state_version=0)
    await session.rollback()

    assert raised.value.quoted == 0
    assert raised.value.current == 1
    assert raised.value.status_code == 409
    assert raised.value.code == "quote_stale"


async def test_the_staleness_check_reads_the_version_under_the_lock(
    session: AsyncSession,
) -> None:
    """ADR 0015: the read that feeds the staleness check comes after the book
    lock, and nothing is written before it."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    with capture_sql() as statements:
        await buy(session, upstream, user_id=user_id)

    lowered = [s.lower() for s in statements]
    lock_at = next(
        i for i, s in enumerate(lowered) if "for update" in s and "market_books" in s
    )
    reads_after = [
        s
        for s in lowered[lock_at:]
        if "market_outcomes" in s and s.lstrip().startswith("select")
    ]
    assert reads_after, (
        "nothing read the outcomes after the book row was locked, so the "
        "price and the staleness comparison were made against a read that "
        "predates the lock"
    )
    assert not writes(lowered[:lock_at]), (
        "this path wrote before it took the book lock:\n"
        f"{writes(lowered[:lock_at])}"
    )


# =========================================================================
# The gate's connection, damaged books, and the edges of the quantization
# =========================================================================
async def test_a_trade_holds_no_connection_while_the_gate_waits_on_market_service(
    session: AsyncSession,
) -> None:
    """The gate's HTTP call runs with no transaction open (D-043's fault on the
    trade path).

    Checked from the session, the pool and Postgres. Remove the rollback in
    `market_status.ensure_trading` and this goes red.
    """
    import asyncio  # noqa: PLC0415

    from core.database import get_engine  # noqa: PLC0415

    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    pool = get_engine().pool
    entered = asyncio.Event()
    release = asyncio.Event()
    seen: list[dict[str, object]] = []

    async def stall() -> None:
        seen.append(
            {
                "session in a transaction": session.in_transaction(),
                "pooled connections checked out": pool.checkedout(),
            }
        )
        entered.set()
        await release.wait()

    upstream.before_response = stall

    task = asyncio.create_task(buy(session, upstream, user_id=user_id))
    try:
        async with asyncio.timeout(10):
            await entered.wait()
        idle = await idle_in_transaction()
    finally:
        release.set()
        trade = await task

    clean = {"session in a transaction": False, "pooled connections checked out": 0}
    assert seen == [clean]
    assert idle == 0, f"{idle} backend(s) idle in transaction across the gate's call"
    assert upstream.calls == 1
    assert trade.state_version == 1


async def test_a_cold_trade_reaches_ensure_open_with_nothing_pending(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trade reaches `books.ensure_open` with nothing pending, recorded at
    the call. Two upstream calls show the cold path ran.
    """
    from service import books as books_module  # noqa: PLC0415

    upstream = Upstream()
    user_id = uuid.uuid4()
    await fund(session, user_id)

    real = books_module.ensure_open
    pending: list[tuple[int, int, int]] = []

    async def recording(s, market_id, **kwargs):  # noqa: ANN001, ANN003
        pending.append((len(s.new), len(s.dirty), len(s.deleted)))
        return await real(s, market_id, **kwargs)

    monkeypatch.setattr(books_module, "ensure_open", recording)

    trade = await buy(session, upstream, user_id=user_id)

    assert pending == [(0, 0, 0)]
    assert upstream.calls == 2
    assert trade.state_version == 1


async def _damage(session: AsyncSession, upstream: Upstream, how: str) -> None:
    from sqlalchemy import delete, update  # noqa: PLC0415

    from unit_test.conftest import strip_outcomes  # noqa: PLC0415

    ents = entities()
    if how == "no_outcomes":
        await strip_outcomes(session, upstream.market_id)
        return
    if how == "one_outcome":
        await session.execute(
            delete(ents.MarketOutcome).where(
                ents.MarketOutcome.market_id == upstream.market_id,
                ents.MarketOutcome.position == 1,
            )
        )
    else:
        await session.execute(
            update(ents.MarketBook)
            .where(ents.MarketBook.market_id == upstream.market_id)
            .values(liquidity_b=Decimal(how))
        )
    await session.commit()


# No `Infinity`: `numeric(18, 4)` refuses to store one, so it cannot be
# sitting in a book. `NaN` it stores happily.
@pytest.mark.parametrize(
    "how", ["no_outcomes", "one_outcome", "0.0000", "-100.0000", "NaN"]
)
async def test_an_incomplete_book_is_market_book_incomplete_and_writes_nothing(
    session: AsyncSession, how: str
) -> None:
    """The trade refuses the books the preview refuses, through the same
    guard, `book_prices.refuse_unpriceable`."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    await _damage(session, upstream, how)
    before = await entry_count(session)

    with pytest.raises(Exception) as raised:
        await buy(session, upstream, user_id=user_id)
    await session.rollback()

    assert isinstance(raised.value, errors().LedgerError), (
        f"an incomplete book raised an unmapped "
        f"{type(raised.value).__name__}: {raised.value!r}"
    )
    assert raised.value.code == "market_book_incomplete"
    assert raised.value.status_code == 500
    assert await _committed_state(entry_count) == before
    assert (
        await _committed_state(lambda s: book_row(s, upstream.market_id))
    ).state_version == 0
    assert (
        await _committed_state(
            lambda s: position_of(s, user_id, upstream.market_id, upstream.outcomes[0])
        )
        is None
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("liquidity_b", "0"),
        ("liquidity_b", "-1"),
        ("liquidity_b", "NaN"),
        ("liquidity_b", "Infinity"),
        ("seed_subsidy", "0"),
        ("seed_subsidy", "-1"),
    ],
)
async def test_terms_that_cannot_open_a_book_are_refused_on_a_cold_trade(
    session: AsyncSession, field: str, value: str
) -> None:
    """`books.ensure_open`'s `b` and subsidy refusals, reached from a cold
    trade: a 503, and nothing written."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await fund(session, user_id)
    upstream.body[field] = value
    before = await entry_count(session)

    with pytest.raises(errors().MarketTermsUnavailable):
        await buy(session, upstream, user_id=user_id)
    await session.rollback()

    assert await _committed_state(entry_count) == before
    assert await _committed_state(lambda s: q_of(s, upstream.market_id)) == []


async def test_a_quantity_whose_resulting_q_exceeds_the_column_is_refused(
    session: AsyncSession,
) -> None:
    """D-040's second half: the resulting `q` must fit the column too, as the
    preview already refuses."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)
    quantity = Decimal("99999999999999.9999")
    before = await entry_count(session)

    with pytest.raises(errors().QuantityTooLarge):
        await buy(session, upstream, user_id=user_id, quantity=quantity)
    await session.rollback()

    assert await _committed_state(entry_count) == before


async def test_the_trade_charges_the_previews_tick_where_ambient_abs_would_not(
    session: AsyncSession,
) -> None:
    """D-042's `copy_abs`, on D-044's book where a bare `abs()` would charge a
    tick less than the preview quoted."""
    upstream = Upstream()
    upstream.body["liquidity_b"] = "300"
    user_id = uuid.uuid4()
    await warm(session, upstream, [Decimal("1000.0000"), Decimal("1100.0000")])
    await fund(session, user_id)

    quote = await preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[0],
        side=pricing().Side.BUY,
        quantity=Decimal("200.0000"),
        access_token=token(),
        terms_client=terms_client_over(upstream.transport),
    )
    assert quote.total == Decimal("-100.0001"), (
        "this book no longer lands on D-044's tick case, so the test below "
        "cannot tell copy_abs from abs"
    )

    trade = await buy(
        session, upstream, user_id=user_id, quantity=Decimal("200.0000")
    )

    assert trade.total == quote.total


async def test_a_buy_that_prices_at_zero_is_cost_below_tick_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """D-041: a buy the engine prices at zero is refused by `quantize_cost`
    itself, as `cost_below_tick`."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    skewed = [ZERO, Decimal("20000.0000")]
    await warm(session, upstream, skewed)
    await fund(session, user_id)
    before = await entry_count(session)

    with pytest.raises(errors().CostBelowTick):
        await buy(
            session, upstream, user_id=user_id, outcome=0, quantity=Decimal("0.0001")
        )
    await session.rollback()

    assert await _committed_state(entry_count) == before
    assert await _committed_state(lambda s: q_of(s, upstream.market_id)) == skewed
