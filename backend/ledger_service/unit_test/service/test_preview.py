"""What a trade will cost, before anybody commits to it. [T-1] #21

Business rules, driven through `service/preview.py` with no HTTP *inbound*.
Status codes, query-string validation and the shape on the wire belong to
`unit_test/controller/test_preview_routes.py`; what is asserted here is the
arithmetic, the reads it makes, and the writes it does and does not perform.

The *outbound* call is stubbed at the transport, the same way
`test_market_books.py` does it, so the cold path exercises the real request,
the real `Authorization` header and the real decimal-string parsing without a
market service running and without importing one — `test_import_boundary.py`
fails any `import market_service` from this suite.

**Three things this file deliberately does not test.**

*Whether the market is still open.* The ledger cannot know: the book stores no
status, `close_time` is not snapshotted, and ADR 0014 leaves `close_time` in
the future on an early close. The issue's Notes say a preview on a closed
market returns a number, and the frontend gates on #62's derived status. How
the ledger learns a market has stopped trading is [T-2] #22's problem.

*Whether the caller holds the shares they are selling.* A sell is priced
arithmetically. The per-user holdings check is [T-3] #23's and only means
anything under the trade's lock (D-012). What *is* checked here is the
no-shorting rule against shares outstanding, which is a property of the book.

*Whether `state_version` increments.* Nothing in this repository increments it
— `MarketBook`'s docstring says so and hands it to #22. The one test that needs
a newer version writes the column directly and says so.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator, Sequence
from decimal import ROUND_HALF_UP, Decimal

import httpx
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine
from core.lmsr import cost_to_trade, prices
from model.entities import Entry
from unit_test.conftest import mint_token


def _preview():
    """Imported inside each test rather than at module scope.

    `service/preview.py` does not exist yet, and a top-level import would be
    one collection error taking the whole file down as a single red line.
    Reached through here, every test fails on its own, named after the
    criterion it holds (D-007).
    """
    from service import preview  # noqa: PLC0415

    return preview


def _pricing():
    """`core/pricing.py`: `quantize_cost` and `Side`. Neither exists yet."""
    from core import pricing  # noqa: PLC0415

    return pricing


def _books():
    from service import books  # noqa: PLC0415

    return books


def _entities():
    from model import entities  # noqa: PLC0415

    return entities


def _errors():
    from core import errors  # noqa: PLC0415

    return errors


ZERO = Decimal(0)
_QUANTUM = Decimal("0.0001")

_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")

# Asymmetric on purpose. With a uniform `q` every outcome prices identically,
# so a bug that read the `q` vector in the wrong order — by insertion, by
# outcome id, by anything but `position` — would be invisible in every
# assertion in this file.
_Q = [Decimal("137.5000"), Decimal("42.2500")]

# Chosen so the raw cost lands off a tick boundary. Asserted rather than
# assumed, in `_assert_rounding_is_load_bearing` below: a quantity whose cost
# happens to be exact at scale 4 would make the rounding tests tautological,
# and that is a property of this number, not of the code under test.
_QUANTITY = Decimal("13.3333")


# --- upstream -------------------------------------------------------------
class _Upstream:
    """A stand-in market service that counts its calls and keeps the token.

    Both are load-bearing. The count separates "the cold path opened the book"
    from "every request re-fetches the terms", which is the difference between
    D-008's accepted one-off cost and a network round trip on every keystroke.
    The token is the D-018 forwarding rule: the ledger sends the caller's own
    credential upstream and mints nothing of its own.
    """

    def __init__(
        self,
        *,
        published_at: str | None = "2026-09-01T09:00:00Z",
        status: str = "open",
        outcomes: int = 2,
    ) -> None:
        self.calls = 0
        self.tokens: list[str] = []
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]
        self._body = {
            "id": str(self.market_id),
            "status": status,
            "close_time": "2027-01-05T12:00:00Z",
            "resolution_time": "2027-01-20T12:00:00Z",
            "liquidity_b": str(_B),
            "seed_subsidy": str(_SUBSIDY),
            "published_at": published_at,
            "outcomes": [
                {"id": str(o), "position": i, "label": f"Outcome {i}"}
                for i, o in enumerate(self.outcomes)
            ],
        }

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            header = request.headers.get("Authorization", "")
            self.tokens.append(header.removeprefix("Bearer "))
            return httpx.Response(200, json=self._body)

        return httpx.MockTransport(handler)

    @property
    def dead(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            raise httpx.ConnectError("market service is down")

        return httpx.MockTransport(handler)


def _token() -> str:
    return mint_token(uuid.uuid4())


# --- driving the thing under test ----------------------------------------
async def _quote(
    session: AsyncSession,
    upstream: _Upstream,
    *,
    outcome: int = 0,
    side: str = "buy",
    quantity: Decimal = _QUANTITY,
    access_token: str | None = None,
    transport: httpx.MockTransport | None = None,
):
    """One preview, with the ticket's parameter names.

    `side` arrives here as the wire string and is converted to `Side` at the
    boundary, because that is what the route does: parsed once, passed down as
    the enum, so the service never re-validates a string the controller has
    already checked.
    """
    return await _preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[outcome],
        side=_pricing().Side(side),
        quantity=quantity,
        access_token=access_token if access_token is not None else _token(),
        transport=upstream.transport if transport is None else transport,
    )


async def _warm(
    session: AsyncSession, upstream: _Upstream, q: Sequence[Decimal] = tuple(_Q)
):
    """A market whose book exists and whose outcomes hold shares.

    `q` is written directly. Nothing in this repository moves it — that is
    [T-2] #22's trade path — so a test that wants a market anybody has traded
    in has to write the state a trade would have left, exactly as
    `test_market_books.py` had to write nothing and price nothing.
    """
    book = await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=_token(),
        transport=upstream.transport,
    )
    await _set_q(session, upstream, q)
    return book


async def _set_q(
    session: AsyncSession, upstream: _Upstream, q: Sequence[Decimal]
) -> None:
    outcome = _entities().MarketOutcome
    for position, value in enumerate(q):
        await session.execute(
            update(outcome)
            .where(
                outcome.market_id == upstream.market_id,
                outcome.position == position,
            )
            .values(q=value)
        )
    await session.commit()


async def _book_row(session: AsyncSession, upstream: _Upstream):
    book = _entities().MarketBook
    session.expire_all()
    return (
        await session.execute(select(book).where(book.market_id == upstream.market_id))
    ).scalar_one()


# --- the expected arithmetic ---------------------------------------------
#
# Recomputed from `core/lmsr.py` and `core/pricing.py` rather than pinned as
# literals. The criterion is "computed from ledger_service/core/lmsr.py, not
# estimated", and `unit_test/core/test_lmsr.py` already pins the engine against
# an independent float implementation — so a literal here would be a third
# restatement of the same number, and the one most likely to be copied out of
# a failing run.
def _delta(q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal):
    delta = [ZERO] * len(q)
    delta[outcome] = quantity if side == "buy" else -quantity
    return delta


def _raw_cost(q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal):
    return cost_to_trade(list(q), _B, _delta(q, outcome, side, quantity))


def _expected_total(q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal):
    """The signed total, built the way the criteria describe it in order.

    Magnitude out of the engine, quantized by side, then the sign applied by
    the caller — negative on a buy because credits leave the trader. Doing it
    in any other order is the bug `quantize_cost`'s unsigned signature exists
    to prevent, and `unit_test/core/test_pricing.py` holds the argument for it.
    That convention is not recorded in DECISIONS.md and probably should be: it
    is a numeric contract at a boundary, and the reason `ROUND_FLOOR` is
    correct for a sell is not recoverable from the rounding mode alone.
    """
    magnitude = _pricing().quantize_cost(
        _raw_cost(q, outcome, side, quantity).copy_abs(), side=_pricing().Side(side)
    )
    return -magnitude if side == "buy" else magnitude


def _expected_prices(q: Sequence[Decimal]) -> list[Decimal]:
    """`ROUND_HALF_UP` at scale 4, with no direction to favour.

    Nobody is charged a price — the money is `total`, and `[T-2] #22` builds
    its legs from that. A directional rule here would bias a displayed
    probability for no one's benefit.
    """
    return [p.quantize(_QUANTUM, rounding=ROUND_HALF_UP) for p in prices(list(q), _B)]


def _assert_rounding_is_load_bearing(
    q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal
) -> None:
    """Guard on the fixture, not on the code.

    Every assertion about rounding below is worthless if the raw cost happens
    to be exact at scale 4. If this fires, change `_QUANTITY` — do not relax
    the assertion it is protecting.
    """
    raw = _raw_cost(q, outcome, side, quantity).copy_abs()
    assert raw != raw.quantize(_QUANTUM), (
        "this fixture's raw cost is already exact at scale 4, so the "
        "quantization tests below prove nothing; pick another quantity"
    )


# --- structural capture ---------------------------------------------------
@contextlib.contextmanager
def _capture_sql() -> Iterator[list[str]]:
    """Every statement the block sends to Postgres, in order.

    Attached to the engine rather than inferred from timings, because the four
    facts below are structural claims and a structural claim deserves a
    structural assertion. `before_cursor_execute` sees the compiled statement
    text, which is what makes "one statement, joining these two tables, with no
    `FOR UPDATE` and no write" checkable without pinning the join syntax, the
    column list or the alias names.
    """
    statements: list[str] = []
    engine = get_engine().sync_engine

    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


def _mentioning(statements: Sequence[str], table: str) -> list[str]:
    return [s for s in statements if table in s.lower()]


def _writes(statements: Sequence[str]) -> list[str]:
    return [
        s
        for s in statements
        if s.strip().lower().startswith(("insert", "update", "delete"))
    ]


# =========================================================================
# What the preview returns
# =========================================================================
async def test_the_total_is_computed_from_the_lmsr_cost_function(
    session: AsyncSession,
) -> None:
    """The second criterion: computed from `core/lmsr.py`, not estimated.

    `C(q + delta) - C(q)` for a one-outcome delta, quantized by side. An
    implementation that multiplied the current marginal price by the quantity
    would agree with this to two or three decimal places on a small trade and
    diverge on a large one, which is exactly the trade where a trader would
    notice being charged something other than what they were quoted.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(session, upstream)

    assert quote.total == _expected_total(_Q, 0, "buy", _QUANTITY)


async def test_the_previewed_total_is_what_the_same_quantization_would_charge(
    session: AsyncSession,
) -> None:
    """The whole point of D-014, pinned: one code path quotes and charges.

    The criterion is "quantized by the same function [T-2] #22 uses to build
    its legs, so the previewed number is the charged number". That function is
    `core/pricing.py::quantize_cost`, and this test spells out the composition
    the trade path will repeat — magnitude from the engine, `quantize_cost` by
    side, sign applied last — and asserts the preview produces exactly it.

    The fixture guard above is what makes this mean anything: the raw cost has
    a non-zero fifth decimal place, so an implementation that quantized
    `ROUND_HALF_UP`, or quantized in the wrong place, or did not quantize at
    all, is off by a tick here rather than agreeing by luck.

    ADR 0005's stated fear is a trader quoted one number and charged another.
    This is the assertion that stands between this repository and that.
    """
    upstream = _Upstream()
    await _warm(session, upstream)
    _assert_rounding_is_load_bearing(_Q, 0, "buy", _QUANTITY)

    quote = await _quote(session, upstream)

    magnitude = _pricing().quantize_cost(
        _raw_cost(_Q, 0, "buy", _QUANTITY).copy_abs(), side=_pricing().Side.BUY
    )
    assert quote.total == -magnitude
    assert abs(quote.total) != _raw_cost(_Q, 0, "buy", _QUANTITY).copy_abs()


async def test_the_total_is_negative_on_a_buy_and_positive_on_a_sell(
    session: AsyncSession,
) -> None:
    """The sign convention, stated from the trader's side.

    Negative means credits leave them, positive means credits arrive. It is the
    opposite sign from `cost_to_trade`'s, deliberately: the engine answers "what
    does this cost the market maker to absorb", and the response answers "what
    happens to your balance", and [X-4] #37 renders the second.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    bought = await _quote(session, upstream, side="buy")
    sold = await _quote(session, upstream, side="sell")

    assert bought.total < ZERO
    assert sold.total > ZERO


@pytest.mark.parametrize("outcome", [0, 1])
@pytest.mark.parametrize("side", ["buy", "sell"])
async def test_it_works_for_buy_and_sell_on_every_outcome(
    session: AsyncSession, side: str, outcome: int
) -> None:
    """The sixth criterion, over the whole cross product.

    Two sides times every outcome, each against the arithmetic recomputed for
    that exact position. A market has more than one outcome and every one of
    them is tradeable in both directions; an implementation that special-cased
    position zero, or that priced the traded outcome against the first `q` in
    the row set, passes a single-outcome test and fails here.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(session, upstream, outcome=outcome, side=side)

    assert quote.total == _expected_total(_Q, outcome, side, _QUANTITY)
    assert quote.outcome_id == upstream.outcomes[outcome]
    assert quote.side is _pricing().Side(side)


async def test_the_q_vector_is_ordered_by_position(session: AsyncSession) -> None:
    """`position`, not insertion order and not the outcome id.

    `market_outcomes` has a composite primary key and no surrogate id (D-034),
    so an unordered `SELECT` hands back rows in whatever order Postgres finds
    them — which is stable enough to pass every other test in this file and
    free to change on a vacuum. With `q` asymmetric, reading the vector
    backwards prices outcome 0 as if it were outcome 1.

    Asserted by pricing a market whose `q` is reversed and checking the answers
    swap rather than stay put.
    """
    upstream = _Upstream()
    await _warm(session, upstream)
    straight = await _quote(session, upstream, outcome=0)

    await _set_q(session, upstream, list(reversed(_Q)))
    reversed_q = await _quote(session, upstream, outcome=0)

    assert straight.total != reversed_q.total
    assert reversed_q.total == _expected_total(
        list(reversed(_Q)), 0, "buy", _QUANTITY
    )


# --- average price --------------------------------------------------------
async def test_average_price_is_the_quantized_total_over_the_quantity(
    session: AsyncSession,
) -> None:
    """The fourth criterion: `abs(total) / quantity`, from the *quantized* total.

    From the quantized total and not from the raw cost, because the average is
    supposed to explain the number the trader is about to pay. An average
    derived from the unrounded cost would not multiply back to the total, and
    the first person to check the arithmetic by hand would file a bug.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(session, upstream)

    expected = (abs(quote.total) / _QUANTITY).quantize(
        _QUANTUM, rounding=ROUND_HALF_UP
    )
    assert quote.average_price == expected


async def test_average_price_is_positive_on_both_sides(
    session: AsyncSession,
) -> None:
    """`abs()`, so it reads as a price rather than as a direction.

    The sign already lives on `total`. A negative average price would be a
    second place for it, disagreeing with the first the moment somebody formats
    one and not the other.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    bought = await _quote(session, upstream, side="buy")
    sold = await _quote(session, upstream, side="sell")

    assert bought.average_price > ZERO
    assert sold.average_price > ZERO


async def test_average_price_rounds_half_up_because_it_is_derived_not_charged(
    session: AsyncSession,
) -> None:
    """Not directional, and the reason is worth stating where somebody will read it.

    `total` is directional because the residue on it is money and it has to go
    to the pool. `average_price` is a display figure derived from that
    authoritative total: [T-2] #22 charges `total`, never
    `quantity * average_price`, so there is no residue here to send anywhere
    and nothing to protect the pool from. `ROUND_HALF_UP` is then the honest
    rounding for a number whose only job is to be read.

    A future reader who "fixes" this to match the cost rounding makes the
    displayed average disagree with the total it was divided out of, in the
    direction that flatters the quote.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(session, upstream)
    exact = abs(quote.total) / _QUANTITY

    assert quote.average_price == exact.quantize(_QUANTUM, rounding=ROUND_HALF_UP)
    assert quote.average_price.as_tuple().exponent == -4


# --- prices ---------------------------------------------------------------
async def test_prices_carry_every_outcome_in_position_order(
    session: AsyncSession,
) -> None:
    """"the market's current prices" — all of them, ordered the way `PriceEvent` is.

    Every outcome, not only the one being traded: a trade moves all of them and
    a client rendering one would show a market whose prices no longer sum to
    one. `position` travels with each price for the reason `OutcomePrice`
    carries it — so a categorical market renders in the order the administrator
    arranged, twice, without a second lookup.
    """
    upstream = _Upstream(outcomes=3)
    q = [Decimal("137.5000"), Decimal("42.2500"), Decimal("88.0000")]
    await _warm(session, upstream, q)

    quote = await _quote(session, upstream)

    assert [p.position for p in quote.prices] == [0, 1, 2]
    assert [p.outcome_id for p in quote.prices] == upstream.outcomes
    assert [p.price for p in quote.prices] == _expected_prices(q)


async def test_post_trade_prices_are_the_prices_the_trade_would_leave(
    session: AsyncSession,
) -> None:
    """"the post-trade price of **every** outcome", computed, not estimated.

    `prices(q + delta, b)`, where `delta` is the trade. Every outcome again,
    because moving one `q_i` moves the whole softmax — an implementation that
    recomputed only the traded outcome would leave the other prices at their
    pre-trade values and show a market that does not add up.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(session, upstream)

    after = [a + d for a, d in zip(_Q, _delta(_Q, 0, "buy", _QUANTITY))]
    assert [p.outcome_id for p in quote.post_trade_prices] == upstream.outcomes
    assert [p.price for p in quote.post_trade_prices] == _expected_prices(after)


async def test_a_buy_raises_the_traded_outcome_s_post_trade_price(
    session: AsyncSession,
) -> None:
    """The direction, asserted separately from the value.

    "how it moves the price before confirming" is the story, and a sign error
    in the delta would still satisfy the equality test above if the same sign
    error were made in this file. This one is stated in terms nobody can get
    backwards: buying an outcome makes it dearer and buying it makes every
    other outcome cheaper.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(session, upstream, outcome=0, side="buy")

    assert quote.post_trade_prices[0].price > quote.prices[0].price
    assert quote.post_trade_prices[1].price < quote.prices[1].price


async def test_post_trade_prices_need_not_sum_to_exactly_one_at_scale_four(
    session: AsyncSession,
) -> None:
    """A tolerance, pinned, rather than an equality that would be a lie.

    LMSR's marginal prices sum to 1 in exact arithmetic. Quantized to scale 4
    they need not: each one absorbs up to half a tick of rounding, so `n`
    outcomes can miss by `n * 0.00005` under `ROUND_HALF_UP`. The engine will
    not normalise them — `prices()` says so in its docstring, and pre-normalised
    numbers would be a second source of truth for a price.

    The bound asserted here is the looser `n * 0.0001`, which also covers a
    truncating rounding rule. That is deliberate: this test exists to catch a
    wrong price *vector* — a missing outcome, a stale `q`, a softmax over the
    wrong denominator, each of which misses by a lot — and not to re-assert the
    rounding mode, which `_expected_prices` above already pins exactly.

    `realtime_service` makes the same refusal from the other end: its
    `PriceEvent` deliberately does not check that prices sum to one, because a
    relay that rejected a correct price over a rounding tolerance would be
    worse than one that relays what the authority said.
    """
    upstream = _Upstream(outcomes=3)
    q = [Decimal("137.5000"), Decimal("42.2500"), Decimal("88.0000")]
    await _warm(session, upstream, q)

    quote = await _quote(session, upstream)

    for vector in (quote.prices, quote.post_trade_prices):
        total = sum((p.price for p in vector), ZERO)
        tolerance = _QUANTUM * len(vector)
        assert abs(total - Decimal(1)) <= tolerance, f"{total} is not near 1"


# --- the round trip -------------------------------------------------------
async def test_buying_then_selling_the_same_quantity_never_profits(
    session: AsyncSession,
) -> None:
    """The property [F-3] #43 holds for the engine, carried to the quantized money.

    `test_lmsr.py` proves the raw magnitudes are equal. That is not enough: the
    round trip happens at scale 4, so the question is whether the two
    quantizations can pull apart in the trader's favour. They cannot, because
    the buy rounds up and the sell rounds down — and this is the test that says
    so in credits rather than in rounding modes.

    The sell is priced against the `q` the buy would have left, which is what
    makes this a round trip rather than two independent quotes. Written
    directly, standing in for [T-2] #22's increment.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    bought = await _quote(session, upstream, side="buy")

    after = [a + d for a, d in zip(_Q, _delta(_Q, 0, "buy", _QUANTITY))]
    await _set_q(session, upstream, after)
    sold = await _quote(session, upstream, side="sell")

    assert sold.total <= abs(bought.total), (
        "selling back what you just bought must never return more credits "
        f"than it cost: paid {abs(bought.total)}, received {sold.total}"
    )


# =========================================================================
# The quote reference
# =========================================================================
async def test_state_version_is_the_book_s_own_counter(
    session: AsyncSession,
) -> None:
    """D-011: the same counter `PriceEvent` and the snapshot report.

    Read off `market_books`, not invented per request. A second counter would
    be a second answer to "has this market moved", and #22's staleness check
    compares the one the trader was quoted against the one under its lock.
    """
    upstream = _Upstream()
    book = await _warm(session, upstream)

    quote = await _quote(session, upstream)

    assert quote.state_version == book.state_version
    assert isinstance(quote.state_version, int)


async def test_a_preview_after_an_intervening_version_bump_returns_the_newer_number(
    session: AsyncSession,
) -> None:
    """The reference tracks the book, rather than being read once and cached.

    The `UPDATE` below stands in for [T-2] #22's increment: nothing in this
    repository moves `state_version` yet, and a test that waited for a real
    trade would be waiting for the ticket this one unblocks. What matters is
    that the row changed under the preview between two calls, which is exactly
    what a committed trade would look like from here.

    A preview that returned a stale version would be worse than one that
    returned none: #22 compares it under lock and would accept a quote taken
    against a `q` that has since moved.
    """
    upstream = _Upstream()
    book = _entities().MarketBook
    await _warm(session, upstream)

    before = await _quote(session, upstream)

    await session.execute(
        update(book)
        .where(book.market_id == upstream.market_id)
        .values(state_version=book.state_version + 1)
    )
    await session.commit()

    after = await _quote(session, upstream)

    assert after.state_version == before.state_version + 1


# =========================================================================
# The no-shorting rule
# =========================================================================
async def test_a_sell_larger_than_that_outcome_s_q_is_refused(
    session: AsyncSession,
) -> None:
    """The seventh criterion, at 409 with its own code.

    Against shares *outstanding*, not against a per-user holding — the ledger
    has no positions table yet and [T-3] #23 owns the holdings check. What this
    refuses is a sell that would drive `q_i` negative, which is not a trade the
    cost function has an answer for: `C(q)` is defined there, so the preview
    would quote a number for shares that do not exist anywhere.

    Refused rather than clamped. A clamp would quote a different trade from the
    one asked for, and the trader would confirm a quantity they never typed.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with pytest.raises(_errors().LedgerError) as raised:
        await _quote(
            session, upstream, outcome=1, side="sell", quantity=_Q[1] + _QUANTUM
        )

    assert raised.value.code == "insufficient_shares_outstanding"
    assert raised.value.status_code == 409


async def test_a_sell_of_exactly_that_outcome_s_q_is_allowed(
    session: AsyncSession,
) -> None:
    """The boundary, on the permitted side.

    Selling every outstanding share returns `q_i` to zero, which is the state
    the market opened in and a perfectly ordinary one to price. An
    implementation that used `>=` refuses the one trade that closes a market
    out, and would do it only on the last sale.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(session, upstream, outcome=1, side="sell", quantity=_Q[1])

    assert quote.total == _expected_total(_Q, 1, "sell", _Q[1])


async def test_a_buy_is_never_refused_for_size(session: AsyncSession) -> None:
    """The rule is about shares outstanding and applies to one side only.

    A buy can be arbitrarily large — LMSR has a price for every quantity, and
    whether the trader can afford it is a balance question that belongs under
    #22's lock, not in a preview. Refusing a big buy here would be the ledger
    deciding whether a trade may happen, which D-014 explicitly says it does
    not do.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(session, upstream, side="buy", quantity=Decimal("100000"))

    assert quote.total < ZERO


async def test_a_sell_is_priced_with_no_holdings_check(
    session: AsyncSession,
) -> None:
    """The sixth criterion's second half: priced arithmetically.

    The caller here holds nothing — there is no positions table in this service
    and no row anywhere says otherwise — and still gets a number, because the
    only thing that constrains a sell in this ticket is shares outstanding. The
    per-user check is [T-3] #23's and, per D-012, only means anything under the
    trade's lock: a preview that checked it would be quoting a refusal that
    could be stale by the time anybody acted on it.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    quote = await _quote(
        session, upstream, outcome=1, side="sell", quantity=Decimal("1.0000")
    )

    assert quote.total > ZERO


# =========================================================================
# The reads it makes
# =========================================================================
async def test_the_pricing_read_is_one_statement_joining_the_two_tables(
    session: AsyncSession,
) -> None:
    """D-013, asserted structurally rather than by timing or by eye.

    Two facts, and the second is the one that catches a lazy load. Exactly one
    statement touches `market_books`, and that same statement also touches
    `market_outcomes` — so `q`, `b` and `state_version` come back under one
    snapshot. Under READ COMMITTED two statements fail in the dangerous
    direction: read `q` first and `state_version` second and a trade committing
    between them hands back a version newer than the `q` that was priced, which
    #22 then finds current and executes against.

    Exactly one, not at most one, pins the ordering too. On a warm market the
    join read comes *first* and `books.ensure_open` is the fallback when it
    returns nothing — not a preamble that checks for the book and then reads it
    again. The issue's Notes leave no room for the second shape: "every one
    after it is a single indexed read".
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with _capture_sql() as statements:
        await _quote(session, upstream)

    books = _mentioning(statements, "market_books")
    assert len(books) == 1, f"the warm path must read once, sent:\n{statements}"
    assert "market_outcomes" in books[0].lower(), (
        "`q` and `state_version` must come back from one statement (D-013), "
        f"and this one does not join them:\n{books[0]}"
    )


async def test_a_warm_preview_takes_no_locks(session: AsyncSession) -> None:
    """D-012, as corrected by D-036: the *pricing read* is unlocked.

    ADR 0015's rule is about reads that decide a write, and it says reads
    feeding no write stay unlocked. A preview decides nothing — the gap to
    confirm is a human one, seconds to minutes, and no lock survives it, which
    is why the staleness check is #22's under its own lock instead.

    The cost of getting this wrong is not correctness but contention: a preview
    fires on every keystroke, and a `FOR UPDATE` here serialises every trader
    typing a quantity into the same market.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with _capture_sql() as statements:
        await _quote(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking == [], f"the pricing read must take no locks:\n{locking}"


async def test_a_warm_preview_writes_nothing(session: AsyncSession) -> None:
    """The fourth structural fact, and the one the other three do not cover.

    A statement count and a lock check both pass against an implementation that
    prices correctly and then writes something on the way out — a cached price,
    a hit counter, a `last_previewed_at`. This is a `GET`, it is the hottest
    read in the system, and the only write it is allowed to perform is the
    first touch that opens a book.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with _capture_sql() as statements:
        await _quote(session, upstream)

    assert _writes(statements) == [], (
        f"a warm preview must not write:\n{_writes(statements)}"
    )


async def test_a_preview_changes_nothing_on_a_warm_market(
    session: AsyncSession,
) -> None:
    """The same claim as state, rather than as statements.

    No ledger entries, no version bump, and the book row identical field by
    field afterwards. Asserted alongside the statement capture rather than
    instead of it, because the two fail differently: a write through a path
    this capture does not see would show up here, and a write that happens to
    restore the same values would show up there.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    before = await _book_row(session, upstream)
    snapshot = (
        before.market_id,
        before.liquidity_b,
        before.seed_subsidy,
        before.pool_account_id,
        before.state_version,
        before.state_changed_at,
        before.opened_at,
    )
    entries_before = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()

    for _ in range(3):
        await _quote(session, upstream)

    after = await _book_row(session, upstream)
    assert (
        after.market_id,
        after.liquidity_b,
        after.seed_subsidy,
        after.pool_account_id,
        after.state_version,
        after.state_changed_at,
        after.opened_at,
    ) == snapshot

    entries_after = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()
    assert entries_after == entries_before


# =========================================================================
# The cold path
# =========================================================================
async def test_the_first_preview_on_a_cold_market_opens_the_book(
    session: AsyncSession,
) -> None:
    """The ninth criterion, and D-037: the preview is a market's first toucher.

    D-008 settled that terms arrive by lazy pull and named no caller, because
    neither #21 nor #22 existed. #21 lands first, so a `GET` in the ledger
    calls another service, writes a book, writes its outcomes and funds a pool
    — then prices from what it just wrote. Refusing instead would show a trader
    an error on the one read whose whole purpose is to be safe to fire on every
    keystroke.
    """
    upstream = _Upstream()

    quote = await _quote(session, upstream)

    assert quote.market_id == upstream.market_id
    assert upstream.calls == 1

    book = await _book_row(session, upstream)
    assert book.liquidity_b == _B
    assert book.state_version == 0

    from service import accounts  # noqa: PLC0415

    assert await accounts.balance_of(session, book.pool_account_id) == _SUBSIDY


async def test_a_cold_market_prices_from_the_book_it_just_opened(
    session: AsyncSession,
) -> None:
    """"and prices from it" — a real number out of a market that opened at `q = 0`.

    Every `q` is zero at book creation, which is what makes the opening price
    uniform: `C(q)` at `q = 0` gives every outcome `1/n`. So the first preview
    on a binary market quotes against 0.5 each, and that is the number this
    asserts — not a placeholder, not a null, not a second request telling the
    client to try again.
    """
    upstream = _Upstream()

    quote = await _quote(session, upstream)

    assert [p.price for p in quote.prices] == _expected_prices([ZERO, ZERO])
    assert [p.price for p in quote.prices] == [Decimal("0.5000"), Decimal("0.5000")]
    assert quote.total == _expected_total([ZERO, ZERO], 0, "buy", _QUANTITY)


async def test_the_second_preview_makes_no_http_call_at_all(
    session: AsyncSession,
) -> None:
    """"once per market ever", as the stronger of the two available claims.

    The weaker one is that the pool is funded once; that is `test_market_books.py`'s.
    This is the one that decides whether the ledger holds a runtime dependency
    on `market_service` forever or only for a market's first touch. Every
    preview after the first must be a single indexed read, or D-008's accepted
    one-off cost becomes a network round trip on every keystroke and a market
    service outage stops trading in markets that already have books.
    """
    upstream = _Upstream()

    await _quote(session, upstream)
    assert upstream.calls == 1

    await _quote(session, upstream)
    await _quote(session, upstream)

    assert upstream.calls == 1, "an existing book must not be re-fetched"


async def test_the_cold_path_forwards_the_caller_s_own_token(
    session: AsyncSession,
) -> None:
    """D-018's Notes, and the reason the route needs `AccessToken` beside `CurrentUser`.

    The ledger sends the trader's own bearer token upstream and mints nothing
    of its own. A token minted here would be this service asserting an identity
    it was not given, on a route that reaches another service — which is the
    service-to-service auth question ADR 0009 deferred to #22, arriving early
    through a read.

    The raw token is therefore a dependency of its own: `CurrentUser` hands the
    route decoded claims, and claims cannot be re-signed into the credential
    the upstream call needs.
    """
    upstream = _Upstream()
    token = _token()

    await _quote(session, upstream, access_token=token)

    assert upstream.tokens == [token]


async def test_the_cold_path_takes_the_handoff_s_locks(
    session: AsyncSession,
) -> None:
    """D-036, asserted rather than asserted-away.

    The criteria say the pricing read takes no locks and, one bullet later,
    that the cold path "writes and takes the handoff's locks". Both are true
    and the second is the one nobody would guess from D-012's title:
    `books.ensure_open` ends in `posting.post`, which holds `PLATFORM` and this
    market's pool `FOR UPDATE` while the funding transaction commits.

    Pinned here so the correction has evidence rather than prose, and so that a
    future reader who deletes a lock to make "preview takes no locks" literally
    true sees a red test naming the entry that explains why it is not.
    """
    upstream = _Upstream()

    with _capture_sql() as statements:
        await _quote(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking, (
        "the first touch funds a pool, and D-015 and ADR 0015 require that "
        "write to be locked; nothing here took a lock"
    )


async def test_a_preview_on_a_closed_market_returns_a_number(
    session: AsyncSession,
) -> None:
    """The issue's Notes, stated as a test so nobody adds the check later.

    The ledger cannot know whether a market is still open: the book stores no
    status, `close_time` is not snapshotted, and ADR 0014 leaves `close_time`
    in the future on an early close, so even snapshotting it would not answer
    the question. The frontend gates on #62's derived status; [T-2] #22 owns
    how the ledger learns, and that is tracked separately.

    Published is the gate on a book, not tradeable — the same rule
    `test_a_closed_market_still_gets_a_book` holds one layer down, and for the
    same reason settlement in [3.4] #12 reads `q` from markets that stopped
    trading weeks earlier.
    """
    upstream = _Upstream(status="closed")

    quote = await _quote(session, upstream)

    assert quote.total < ZERO


# =========================================================================
# Refusals
# =========================================================================
async def test_an_unknown_outcome_id_is_refused_as_unknown_outcome(
    session: AsyncSession,
) -> None:
    """The tenth criterion: checked against the book, and 422 rather than 404.

    422 because the market was found and the *parameter* is wrong. A 404 would
    say the market does not exist, which is the answer `market_not_found`
    already owns for a genuinely absent market — and a client cannot tell "I
    sent a bad outcome id" from "this market is gone" if both arrive as 404,
    which are two different bugs with two different fixes.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with pytest.raises(_errors().LedgerError) as raised:
        await _preview().quote(
            session,
            upstream.market_id,
            outcome_id=uuid.uuid4(),
            side=_pricing().Side.BUY,
            quantity=_QUANTITY,
            access_token=_token(),
            transport=upstream.transport,
        )

    assert raised.value.code == "unknown_outcome"
    assert raised.value.status_code == 422


async def test_an_unknown_outcome_on_a_cold_market_leaves_the_funded_book_behind(
    session: AsyncSession,
) -> None:
    """Deliberate, and stated so it is not mistaken for a leak.

    The criteria order the checks: `side`, `quantity` and the market id are
    validated before any HTTP call or write; `outcome_id` is checked "against
    the book once it exists". So a bad outcome id on a never-touched market
    opens the book, funds the pool, and then refuses.

    That is the right trade. The market is real and published — the terms pull
    proved it — and the book it just wrote is the same book the next honest
    request would have created. Rolling it back would throw away a correct,
    idempotent, once-per-market write because a query string was wrong, and
    would leave the next caller paying the terms timeout again.
    """
    upstream = _Upstream()

    with pytest.raises(_errors().LedgerError) as raised:
        await _preview().quote(
            session,
            upstream.market_id,
            outcome_id=uuid.uuid4(),
            side=_pricing().Side.BUY,
            quantity=_QUANTITY,
            access_token=_token(),
            transport=upstream.transport,
        )
    assert raised.value.code == "unknown_outcome"

    book = await _book_row(session, upstream)
    assert book.liquidity_b == _B

    from service import accounts  # noqa: PLC0415

    assert await accounts.balance_of(session, book.pool_account_id) == _SUBSIDY

    later = await _quote(session, upstream)
    assert later.total < ZERO
    assert upstream.calls == 1, "the surviving book must serve the next request"


async def test_an_unreachable_market_service_leaves_nothing_behind(
    session: AsyncSession,
) -> None:
    """D-030's 503, and the state afterwards.

    `test_market_terms.py` owns which exception an unreachable upstream maps
    to. What this owns is that a preview which failed that way wrote nothing —
    no book, no pool account, no entries — because a partial book is worse than
    a clean failure: it makes the *next* request take the fast path and price
    from a book that was never finished.
    """
    upstream = _Upstream()

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _quote(session, upstream, transport=upstream.dead)

    await session.rollback()

    book = _entities().MarketBook
    assert (
        await session.execute(
            select(book).where(book.market_id == upstream.market_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one() == 0


async def test_an_unpublished_market_gets_no_quote(session: AsyncSession) -> None:
    """The eleventh criterion reuses [F-7] #96's codes rather than inventing any.

    A draft or a submitted market has no book and must not get one — `b` and
    the subsidy are only immutable once publication has frozen them, and a book
    copied from a draft would snapshot terms an administrator can still edit.
    The preview does not restate that rule; it inherits whatever
    `books.ensure_open` decides, which is what keeps one answer to "may this
    market have a book".
    """
    upstream = _Upstream(published_at=None)

    with pytest.raises(_errors().MarketNotPublished):
        await _quote(session, upstream)


# =========================================================================
# The invariant
# =========================================================================
async def test_the_ledger_still_sums_to_zero_after_a_run_of_previews(
    session: AsyncSession,
) -> None:
    """The strongest single assertion available about this service, after the
    one path in this ticket that moves money.

    Three markets opened by preview, each funded once, and every entry ever
    written summed. If a subsidy was credited without being debited, or a
    preview wrote a leg of its own, this is not zero.
    """
    for _ in range(3):
        upstream = _Upstream()
        await _quote(session, upstream)
        await _quote(session, upstream)

    written = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()
    assert written == 6, "three fundings of two legs each"

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO


# =========================================================================
# The two edges of the quantization, both reachable
# =========================================================================
# A saturated outcome, from `core/lmsr.py::cost_to_trade`'s own docstring:
# 100 shares against `q = [1560, *]` at `b = 100` is worth about 0.0000288.
_SATURATED = [Decimal("1560.0000"), Decimal("100.0000")]
_HUNDRED = Decimal("100.0000")


async def test_a_sub_tick_sell_is_refused_rather_than_quoted_at_zero(
    session: AsyncSession,
) -> None:
    """D-041. Proceeds that quantize to zero are refused, not quoted.

    `quantize_cost` floors a sell's magnitude (D-039), so proceeds under one
    tick reach `0.0000`, and quoting that takes real shares for nothing —
    the surprise this ticket exists to prevent. The alternative, paying a
    minimum tick, hands the trader more than the shares are worth and breaks
    "the residue accrues to the pool, never to the trader", so refusing is the
    only answer that keeps both rules. It mirrors `QuantityTooLarge` (D-040):
    a quote the column cannot honestly represent is refused, at either edge of
    the quantization.

    The buy assertion is the other half, not a spare. The same 100 shares
    still cost a full tick, because `ROUND_CEILING` rounds toward the house
    and a buy above zero needs no refusal — so this is not a rule about
    sub-tick trades, it is a rule about totals of *zero*, and an
    implementation that refused every sub-tick buy would fail here.

    The refusal is raised by `core/pricing.py::quantize_cost` rather than by
    this service, because [T-2] #22 has to make the same
    refusal on the write path: a rule about money that lives only in the
    preview is a rule #22 inherits by copying it or by forgetting to. This
    test drives it through `quote`, which is the assertion that the preview
    actually calls it; `unit_test/core/test_pricing.py` holds the rule itself.
    """
    upstream = _Upstream()
    await _warm(session, upstream, _SATURATED)

    raw = _raw_cost(_SATURATED, 1, "sell", _HUNDRED).copy_abs()
    assert ZERO < raw < _QUANTUM, (
        "this fixture is meant to price a real trade at under one tick; "
        f"got {raw}, so the assertions below prove nothing"
    )

    with pytest.raises(_errors().ProceedsBelowTick):
        await _quote(session, upstream, outcome=1, side="sell", quantity=_HUNDRED)

    buy = await _quote(session, upstream, outcome=1, side="buy", quantity=_HUNDRED)

    assert buy.total == -_QUANTUM


async def test_a_quantity_that_prices_above_the_column_is_refused(
    session: AsyncSession,
) -> None:
    """D-040. A cost `Numeric(18, 4)` cannot hold is refused, not returned.

    `total` is quoted as the number [T-2] #22 will charge, and #22 writes it
    into `Numeric(18, 4)` — 14 integer digits. A quantity of 1e15 prices at 15
    of them, so returning it quotes a trade whose confirm step is a
    `NumericValueOutOfRange`: a 500 arriving *after* the trader committed to a
    quote this service answered 200 to.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with pytest.raises(_errors().QuantityTooLarge):
        await _quote(
            session, upstream, quantity=Decimal("1000000000000000.0000")
        )


async def test_an_ordinary_large_quantity_is_still_quoted(
    session: AsyncSession,
) -> None:
    """The ceiling is the column's, not a guess at what a trade should be.

    Without this, D-040's refusal could tighten to any round number and no
    test would notice. A cost of ~1e13 is absurd as a trade and entirely
    storable, so it must come back priced.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    result = await _quote(session, upstream, quantity=Decimal("10000000000000.0000"))

    assert abs(result.total) <= _pricing().MAX_MAGNITUDE
    assert result.total < ZERO


# =========================================================================
# Review fixes on PR #108
# =========================================================================
async def _set_b(session: AsyncSession, upstream: _Upstream, b: Decimal) -> None:
    """`b` written directly, as `_set_q` writes `q`: `_Upstream` serves one `b`."""
    book = _entities().MarketBook
    await session.execute(
        update(book).where(book.market_id == upstream.market_id).values(liquidity_b=b)
    )
    await session.commit()


async def test_a_sell_s_magnitude_is_not_rounded_up_by_the_ambient_context(
    session: AsyncSession,
) -> None:
    """`abs()` rounds at the ambient 28 digits; `copy_abs()` does not round.

    The engine answers `-99.99999999999999999999999999993169...` at 50
    digits. At 28 that rounds up to `100.0000...`, and `ROUND_FLOOR` then
    pays the trader a tick more than the proceeds.
    """
    upstream = _Upstream()
    q = [Decimal("7000"), Decimal("0")]
    await _warm(session, upstream, q)

    raw = cost_to_trade(q, _B, [Decimal(-100), ZERO]).copy_abs()
    assert raw < Decimal(100), f"the engine's own answer must be under 100; got {raw}"

    quote = await _quote(
        session, upstream, outcome=0, side="sell", quantity=Decimal("100")
    )

    assert quote.total == Decimal("99.9999")


@pytest.mark.parametrize(
    ("q", "b"),
    [
        ([Decimal("12000"), Decimal("1")], Decimal("100")),
        ([Decimal("1200"), Decimal("1")], Decimal("10")),
    ],
)
async def test_a_buy_priced_at_exactly_zero_is_refused(
    session: AsyncSession, q: list[Decimal], b: Decimal
) -> None:
    """100 real shares for zero credits, refused rather than quoted."""
    upstream = _Upstream()
    await _warm(session, upstream, q)
    await _set_b(session, upstream, b)

    with pytest.raises(_errors().LedgerError) as raised:
        await _quote(session, upstream, outcome=1, side="buy", quantity=Decimal("100"))

    assert raised.value.code == "cost_below_tick"
    assert raised.value.status_code == 422


async def test_a_buy_whose_resulting_q_the_column_cannot_hold_is_refused(
    session: AsyncSession,
) -> None:
    """The cost is one tick and fits; the `q` it leaves is 15 integer digits."""
    upstream = _Upstream()
    await _warm(session, upstream, [Decimal("99999999999999.9999"), ZERO])

    with pytest.raises(_errors().LedgerError) as raised:
        await _quote(session, upstream, outcome=0, quantity=Decimal("0.0001"))

    assert raised.value.code == "quantity_too_large"
    assert raised.value.status_code == 422


async def test_a_quantity_beyond_the_ambient_precision_is_refused_not_raised(
    session: AsyncSession,
) -> None:
    """`1E+25` quantized at 28 digits is `InvalidOperation`, which is a 500."""
    upstream = _Upstream()
    await _warm(session, upstream)

    with pytest.raises(_errors().LedgerError) as raised:
        await _quote(session, upstream, quantity=Decimal("1E+25"))

    assert raised.value.code == "quantity_too_large"


async def test_a_raw_string_buy_is_priced_as_a_buy(session: AsyncSession) -> None:
    """`"buy" is Side.BUY` is False, so an unconverted buy priced as a sell."""
    upstream = _Upstream()
    await _warm(session, upstream)

    enum = await _quote(session, upstream, side="buy")
    raw = await _preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[0],
        side="buy",
        quantity=_QUANTITY,
        access_token=_token(),
        transport=upstream.transport,
    )

    assert raw.total == enum.total


async def test_a_raw_string_sell_still_meets_the_no_shorting_rule(
    session: AsyncSession,
) -> None:
    upstream = _Upstream()
    await _warm(session, upstream)

    with pytest.raises(_errors().InsufficientSharesOutstanding):
        await _preview().quote(
            session,
            upstream.market_id,
            outcome_id=upstream.outcomes[1],
            side="sell",
            quantity=_Q[1] + _QUANTUM,
            access_token=_token(),
            transport=upstream.transport,
        )
