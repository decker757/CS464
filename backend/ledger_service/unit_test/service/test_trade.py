"""Buying shares. [T-2] #22

Business rules, driven through `service/trading.py` with no HTTP *inbound*.
Status codes, body validation and the shape on the wire belong to
`unit_test/controller/test_trade_routes.py`; what is asserted here is the
arithmetic, the rows it writes, and the rows it does not.

The *outbound* call is stubbed at the transport, the same way
`test_market_status.py` and `test_preview.py` do it, so the gate exercises the
real request, the real `Authorization` header and the real parsing without a
market service running and without importing one —
`unit_test/test_import_boundary.py` fails any `import market_service` from
this suite.

**Four things live in other files and are deliberately not here.** The races
and the ordering claims are `test_trade_concurrency.py`'s, because they need a
session per party. Everything about a retry is `test_trade_replay.py`'s. The
publish is `test_trade_publish.py`'s. `posting.post`'s new guard is
`test_posting_pending_writes.py`'s, on its own because `service/posting.py` is
Ernest's and the guard lands in its own commit.

**Why the money assertions compare exact Decimals and never approximate.**
The route's contract is that the previewed number is the charged number. An
`abs(a - b) < epsilon` here would pass on a buy that floored, which is the one
rounding mistake this path can make in the trader's favour, and it would pass
on a total that had been through a float. Every comparison below is `==` on a
`Decimal`, against a value recomputed from `core/lmsr.py` and
`core/pricing.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
    """Run an assertion from a session of its own.

    A rejected trade leaves its writes pending in the caller's session, and
    `get_session` rolls them back when the request unwinds. Asking that same
    session what the database holds would therefore be asking about the
    rollback rather than about the commit. A second session, under READ
    COMMITTED, sees only what landed — which is the claim "writes nothing"
    actually makes.
    """
    async with session_factory()() as other:
        return await fn(other)


# =========================================================================
# The constants these tests are built on
# =========================================================================
def test_the_rounding_direction_is_observable_in_these_fixtures() -> None:
    """Not a test of the trade path. A test of the numbers it is driven with.

    Every rounding assertion below compares the charged total against the
    ceiling and against the floor and asserts it is the ceiling. If the raw
    cost happened to be exact at scale 4 those two are the same number and all
    of them pass on an implementation that floors. This fails first, and says
    so, if anybody ever edits `B` or the quantities into something rounder.
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
    """The first criterion, and the whole reason preview and trade share a
    code path ("The preview endpoint lives on the ledger as a read").

    The preview is taken first, against the same committed state, and the
    trade is then asserted to charge that exact number. Not a recomputation
    that happens to agree — the quote object itself, so a divergence between
    the two code paths fails here rather than being discovered by a trader
    whose balance moved by the wrong amount.
    """
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
        transport=upstream.transport,
    )

    before = await balance_of_user(session, user_id)
    trade = await buy(
        session, upstream, user_id=user_id, state_version=quote.state_version
    )

    assert trade.total == quote.total
    assert await balance_of_user(session, user_id) == before + quote.total


async def test_the_cost_rounds_ceiling_and_not_floor(session: AsyncSession) -> None:
    """"A buy rounds ceiling", asserted as a direction rather than a value.

    The charged total is compared against both roundings of the same raw cost.
    Asserting only that it equals the ceiling would also pass if the two were
    equal; asserting that it is *not* the floor is what makes this a statement
    about which way the residue runs. Under one tick either way, and it
    belongs to the pool, which is the side LMSR already expects to lose money.
    """
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
    """Four decimal places exactly, because that is what `Numeric(18, 4)`
    holds and what the fingerprint in `posting.post` hashes. A total carrying
    a fifth place would be rounded by Postgres on the way in, and a retry
    computing it the other way would not match the stored transaction."""
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
    """A kind of its own, so a trade does not render on somebody's statement
    as a welcome bonus — the argument `MARKET_SEED` already made. A Python
    change and nothing else: the column is a non-native `Enum`, so a new
    member needs no migration."""
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
    """A trade moves one outcome's shares outstanding and leaves the rest
    alone. The untraded outcome is asserted explicitly because a `q` vector
    written by index rather than by `position` would move the wrong one, and
    with an asymmetric `q` that is the bug this fixture exists to expose."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    await buy(session, upstream, user_id=user_id, outcome=0)

    assert await q_of(session, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]


async def test_the_state_version_rises_by_exactly_one(
    session: AsyncSession,
) -> None:
    """D-011's quote reference, incremented once per trade under the book
    row's lock. The response reports the *post-trade* value, which is what a
    client needs to quote its next trade against."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    before = (await book_row(session, upstream.market_id)).state_version
    trade = await buy(session, upstream, user_id=user_id, state_version=before)

    after = (await book_row(session, upstream.market_id)).state_version
    assert after == before + 1
    assert trade.state_version == after


async def test_state_changed_at_advances_to_the_transactions_own_moment(
    session: AsyncSession,
) -> None:
    """The fourth of the four writes.

    D-029 pins `state_changed_at == opened_at` at book creation, on the
    grounds that creating the book was the last state change there had been.
    A trade is the next one, and the moment it names is the transaction's own
    `occurred_at` rather than a second clock read — the snapshot route serves
    this column as `occurred_at`, so two different values here would put a
    price and its timestamp a few milliseconds out of step.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    before = (await book_row(session, upstream.market_id)).state_changed_at
    trade = await buy(session, upstream, user_id=user_id)

    book = await book_row(session, upstream.market_id)
    transaction = await session.get(entities().Transaction, trade.transaction_id)
    assert book.state_changed_at > before
    assert book.state_changed_at == transaction.occurred_at


# =========================================================================
# The position
# =========================================================================
async def test_a_buy_writes_the_position_with_its_quantity_and_cost_basis(
    session: AsyncSession,
) -> None:
    """The shares bought and the credits paid for them.

    `cost_basis` is the *magnitude* — a positive number of credits — not the
    signed total, because "`cost_basis` is stored; average entry price is
    derived" makes [T-4] #24's average `cost_basis / quantity`, and a negative
    basis would give a negative average price.
    """
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
    """"#22 only ever adds to a position", and the primary key on the triple is
    what makes that one row rather than two.

    The second buy is priced against the `q` the first one left, so the two
    costs differ — which is the point. A `cost_basis` that summed two copies
    of the first cost, or overwrote rather than added, both pass a test using
    one quantity twice against an unmoved book.
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
    """`outcome_id` is part of the key, so a trader holding both sides holds
    two rows. A key of `(user_id, market_id)` alone would silently merge them
    and [T-4] #24 would report one position at a nonsense average price."""
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

    # Read off `first` before the second lookup: `position_of` expires the
    # whole session, and an expired attribute read afterwards is a lazy load
    # outside the greenlet — `MissingGreenlet`, not an assertion.
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
    """"Shares keep money's scale of 4, in `q` and in positions". A quantity
    arrives at scale 4 and addition at scale 4 is closed, so nothing a trader
    was quoted for is rounded between the quote and the row."""
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
    """`trade:<user_id>:<market_id>:<client key>`, and the client's string is
    never the stored key.

    The hole this closes is not hypothetical: a verbatim key lets a trader
    submit a trade named `signup-grant:<somebody else's user id>`, after which
    that user's first balance read finds the key present, takes
    `ensure_granted`'s early return, and never receives their starting
    credits — permanently, in a table nothing rewrites.
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
    """The attack the derivation exists to refuse, driven rather than argued.

    A trader sends a client key spelled exactly like another user's signup
    grant. The derivation namespaces it, so the victim's grant key is
    untouched and their first balance read still mints their credits.
    """
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
    """The `user_id` component, asserted by removing the only thing that
    separates the two requests. Both trades execute, both are charged, and
    neither is answered with the other's transaction."""
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
    """`context` is the only durable description of a trade, written inside
    `post`'s commit. If a field the response needs is missing from it, the
    fresh path can still answer and every replay afterwards cannot."""
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
    """"One function builds the response body from a `Transaction`", and the
    fresh path calls it too.

    Asserted by building the result a second time from the stored row and
    comparing. Two builders would eventually disagree, and the case where they
    disagree is the retry — which is exactly the case nobody exercises by
    hand.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    trade = await buy(session, upstream, user_id=user_id)

    transaction = await session.get(entities().Transaction, trade.transaction_id)
    assert trading().result_of(transaction) == trade


async def test_the_response_carries_no_prices(session: AsyncSession) -> None:
    """A replayed price was true once and is a lie afterwards, unlike `total`,
    which is what the trader was charged for ever. Prices are the realtime
    contract's — the `price` frame, or the snapshot endpoint.

    Asserted over the attribute names rather than against a fixed field list,
    so a `prices`, `post_trade_prices` or `average_price` added later fails
    here instead of shipping a stale number into a replay.
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
    """D-015's "book row before account rows", now a live rule rather than a
    vacuous one, because this ticket runs `post` *inside* the book lock.

    Both locks are held at once, so the order they are taken in is what stops
    two trades by one user in two different markets deadlocking against each
    other. Asserted structurally: the first `FOR UPDATE` this path issues
    names `market_books`, and the account locks come after it.
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
    """A market nobody has touched still trades.

    `books.ensure_open` ends in `posting.post` and therefore commits, so it
    runs before the book lock is taken — running it inside the trade's
    transaction would end that transaction and drop the row lock mid-trade.
    What this asserts is the outcome: the book exists, it is funded, and the
    trade executed against it.
    """
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
    """`grants.ensure_granted` also ends in `post` and also commits, so it too
    is ordered ahead of the lock. Without it a brand-new trader's first trade
    is refused `insufficient_funds` against an account holding nothing."""
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
    """ADR 0017's hop: one call per trade that is not a replay, forwarding the
    credential the caller presented and minting nothing. The warm book means
    the terms pull is not what is being counted here."""
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
    """ADR 0017's `409 market_closed`, on a book that has existed for weeks.

    The market closes *after* its book was opened, which is the shape an early
    close has in production and the only shape that proves the gate is a live
    read rather than something snapshotted at first touch.
    """
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


@pytest.mark.parametrize("status", ["closed", "pending_resolution", "approved"])
async def test_every_not_open_status_is_refused(
    session: AsyncSession, status: str
) -> None:
    """One comparison against `"open"`, not a list of the statuses that
    happened to exist when it was written. `settled` arrives with [3.4] #12
    and a list-based gate would start accepting trades against settled markets
    that morning."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    upstream.closes(status=status)

    with pytest.raises(errors().MarketClosed):
        await buy(session, upstream, user_id=user_id)
    await session.rollback()


async def test_an_insufficient_balance_is_refused_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """`insufficient_funds` (409), raised by `posting._refuse_overdrafts`
    under the account lock.

    The book writes are already pending in the session when `post` raises, so
    this is also the rollback test for the "all four writes or none" criterion:
    `q`, `state_version` and the position must not survive a refused payment.
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
    """`unknown_outcome` (422), inherited from the preview. The market was
    found and it is the parameter that is wrong, which is why this is not a
    404 — 404 already means "no such market" on this service."""
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
            transport=upstream.transport,
        )
    await session.rollback()

    assert await _committed_state(entry_count) == before
    assert await _committed_state(lambda s: q_of(s, upstream.market_id)) == list(Q)


async def test_a_quantity_that_prices_above_the_column_is_refused(
    session: AsyncSession,
) -> None:
    """`quantity_too_large` (422), D-040's bound, checked on the *cost*.

    The trade path has to make this refusal for the reason the preview does:
    a `total` of fifteen integer digits is a number `Numeric(18, 4)` cannot
    store, so writing it is a `NumericValueOutOfRange` — a 500 arriving after
    the trader committed to a quote this service answered 200 to.
    """
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
    """`market_terms_unavailable` (503). The gate fails closed: an unreachable
    market service refuses the trade rather than letting it run on a stale
    answer, which is ADR 0017's argument that an outage narrows the window
    rather than widening it."""
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
    """404 on a cold market the upstream does not know. A market that already
    holds a book is the other case and is 503, because that market was
    published and nothing here deletes one — `test_market_status.py` owns that
    distinction and this only checks the trade path inherits it."""
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
    """Strict equality on `state_version`, the only staleness protection the
    trade has ("The trade's staleness check is strict `state_version`
    equality, and the field is required").

    The book is moved by one real trade first, so the stale quote is stale for
    the reason a trader's would be.
    """
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
    """Strict equality means both directions.

    A `>=` comparison would read as "as fresh or fresher" and is the natural
    thing to write. It accepts a version that does not exist yet, which is a
    client that has invented its quote reference rather than read one, and it
    accepts every replayed-from-the-future body a broken client can send.
    """
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
    """`error.details` with `quoted` and `current`, so a client can re-preview
    and retry without guessing which way it was wrong. A bare 409 leaves the
    frontend rendering "something moved" with nothing to resume from."""
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
    """ADR 0015: a check that has to hold under the lock is issued *after* the
    lock statement.

    Asserted structurally, because the alternative — reading `state_version`
    before taking the book row — is a read whose answer another trade can
    invalidate before this one writes, which is the whole failure ADR 0015
    exists for. The book lock appears in the statement log before the read
    that feeds the comparison.
    """
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
# Reconciled with dev after #108 and #110
# =========================================================================
async def _idle_in_transaction() -> int:
    """Backends of this role in this database sitting `idle in transaction`.

    `test_preview.py`'s query, from a connection of its own: a session that
    ran a statement and has not ended its transaction sits in this state for
    as long as it waits, which is exactly a pooled connection held across a
    call to somebody else.
    """
    from sqlalchemy import text  # noqa: PLC0415

    from core.database import get_engine  # noqa: PLC0415

    async with get_engine().connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND usename = current_user "
                    "AND state = 'idle in transaction' "
                    "AND pid <> pg_backend_pid()"
                )
            )
        ).scalar_one()


async def test_a_trade_holds_no_connection_while_the_gate_waits_on_market_service(
    session: AsyncSession,
) -> None:
    """The gate's HTTP call runs with no transaction open.

    The unlocked replay lookup autobegins a transaction, and `get_session`
    does not wrap a request in one of its own, so nothing ended it: the gate
    then called market_service with a pooled connection `idle in transaction`
    for as long as the call took — up to the terms timeout, on every trade
    that is not a replay, against a `pool_size` of 10. "The cold path holds
    no connection across the terms pull" is the same fault on the preview.

    A warm book and a funded trader, so the gate's call is the only one and
    the probe runs at exactly that call. Three views of one fact: the
    session's own `in_transaction()`, the pool's `checkedout()`, and
    Postgres's `pg_stat_activity` while the call is stalled. **Remove the
    rollback after the replay miss in `trading.execute` and this goes red.**
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
        idle = await _idle_in_transaction()
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
    """`books.ensure_open` asserts nothing is pending on the session, because
    its cold path rolls back before the terms pull. #22's order satisfies it
    by construction — the replay lookup and the gate only read — and this
    records the session at the moment of the call rather than trusting that.

    Two upstream calls, so the cold path really ran: the gate's, then the
    terms pull's.
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
    """The trade refuses the books the preview refuses, for the same reasons,
    through the same guard — `book_prices.refuse_unpriceable`, called on the
    trade's own read under the book lock.

    Before it did, a book with no outcome rows was `unknown_outcome` (422),
    blaming the request for the ledger's own damaged data, and an unpriceable
    `b` was a bare `ValueError` out of `core/lmsr.py` — an unmapped 500.
    """
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
    """dev's `b` and `seed_subsidy` refusals, reached from the trade path.

    The gate passes — the market is open — and `books.ensure_open` then
    refuses the terms before it writes a book, as the 503 the contract
    promises. Nothing is written: no book, no pool funding, no trade.
    """
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
    """D-040's second half: the resulting `q`, not only the cost.

    Every digit of this quantity fits `Numeric(18, 4)`, and so does its cost
    — but added to the `q` already outstanding it does not, so the book write
    would be a `NumericValueOutOfRange`. The preview refuses this exact
    request as `quantity_too_large`; the trade has to agree, rather than
    answering `insufficient_funds` for a trade no balance could ever make
    storable.
    """
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
    """D-042's `copy_abs`, on the one book where it changes the charge.

    D-044's own example: at `q = [1000, 1100]`, `b = 300`, buying 200 of
    outcome 0 costs exactly 100, and the engine returns
    `100.000…0001` with the one in its 49th place. `copy_abs` keeps that
    digit and `ROUND_CEILING` charges `100.0001`, which is what the preview
    quotes. A bare `abs()` rounds to the ambient 28 digits first, drops it,
    and charges `100.0000` — a trade that disagrees with its own preview by a
    tick, which is the one thing this route promises never to do.
    """
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
        transport=upstream.transport,
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
    """`quantize_cost` refuses a result of `0.0000` itself, since #108 —
    there is no separate refusal for the trade path to forget to call.

    Past about 110·b of skew the engine prices a small buy of the unlikely
    outcome at exactly zero. Charging nothing for real shares is the
    surprise D-041 exists to prevent, and on a buy the code names the cost,
    not the proceeds.
    """
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
