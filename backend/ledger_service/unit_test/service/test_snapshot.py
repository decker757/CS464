"""The authoritative price read. [F-9] #112

`service/snapshot.py::snapshot` — what a client renders when it opens a page,
and what it re-fetches on every reconnect. Business rules only: status codes
and the JSON shape belong to
`unit_test/controller/test_snapshot_routes.py`.

Driven with no HTTP *inbound* and the outbound call stubbed at the transport,
the same way `test_preview.py` and `test_market_books.py` do it — so the cold
path exercises the real request, the real `Authorization` header and the real
decimal-string parsing without a market service running, and without importing
one.

**Three claims here are structural, and are asserted structurally.** One joined
statement (D-013), no `FOR UPDATE` on the warm path (D-012 as corrected by
D-036), and no write on the warm path. A timing assertion or a careful reading
would cover none of them: `before_cursor_execute` sees the compiled statement
text, which is what makes "one statement, joining these two tables, with no
lock" checkable without pinning the join syntax or the column list.

**The one this file exists for is the absence of a gate.** ADR 0017 states it
directly — "the realtime snapshot serves a closed market's prices" — and it is
the kind of rule that is satisfied by writing nothing and broken by somebody
adding three honest-looking lines. A closed market still renders, a settled one
still shows a last price, and [X-4] #37's reconnect step 2 is a `GET` that has
to return a number or the client has nothing to resume from. Gating would put
an error exactly where a price belongs.

**What this file does not test.** That `state_version` ever increments —
nothing in this repository moves it, `MarketBook`'s docstring hands it to [T-2]
#22, and the test that needs a moved book writes the column directly and says
so.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import httpx
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine
from core.lmsr import prices as lmsr_prices
from model.entities import Entry
from unit_test.conftest import mint_token


def _snapshot_service():
    """Imported inside each test rather than at module scope.

    `service/snapshot.py` does not exist yet, and a top-level import would be
    one collection error taking the whole file down as a single red line.
    Reached through here, every test fails on its own, named after the
    criterion it holds (D-007).
    """
    from service import snapshot  # noqa: PLC0415

    return snapshot


def _books():
    from service import books  # noqa: PLC0415

    return books


def _entities():
    from model import entities  # noqa: PLC0415

    return entities


def _errors():
    from core import errors  # noqa: PLC0415

    return errors


_QUANTUM = Decimal("0.0001")
_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")

# Asymmetric, for `test_preview.py`'s reason: with a uniform `q` every outcome
# prices identically, so a bug that read the vector in the wrong order — by
# insertion, by outcome id, by anything but `position` — would be invisible in
# every assertion in this file.
_Q = [Decimal("137.5000"), Decimal("42.2500")]


# --- upstream -------------------------------------------------------------
class _Upstream:
    """A stand-in market service that counts its calls and keeps the token.

    The count is what separates "the cold path opened the book" from "every
    snapshot asks market_service something", and the second of those is the
    shape this ticket must not have: a snapshot fires on every page open and
    on every reconnect, and ADR 0017's hop belongs to the trade path alone.

    The token is D-018's forwarding rule — the ledger sends the caller's own
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

    @property
    def missing(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return httpx.Response(404, json={"error": {"code": "market_not_found"}})

        return httpx.MockTransport(handler)


def _token() -> str:
    return mint_token(uuid.uuid4())


# --- driving the thing under test ----------------------------------------
async def _read(
    session: AsyncSession,
    upstream: _Upstream,
    *,
    access_token: str | None = None,
    transport: httpx.MockTransport | None = None,
):
    """One snapshot, with the ticket's parameter names.

    The same signature shape as `preview.quote` beside it — the market id
    positional, the caller's own token forwarded, and `transport` injectable so
    the cold path can be driven without a market service.
    """
    return await _snapshot_service().snapshot(
        session,
        upstream.market_id,
        access_token=access_token if access_token is not None else _token(),
        transport=upstream.transport if transport is None else transport,
    )


async def _warm(
    session: AsyncSession, upstream: _Upstream, q: Sequence[Decimal] = tuple(_Q)
):
    """A market whose book exists and whose outcomes hold shares.

    `q` is written directly. Nothing in this repository moves it — that is
    [T-2] #22's trade path — so a test that wants a market anybody has traded
    in has to write the state a trade would have left.
    """
    book = await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=_token(),
        transport=upstream.transport,
    )
    await _set_q(session, upstream, q)
    upstream.calls = 0
    upstream.tokens.clear()
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


def _expected_prices(q: Sequence[Decimal]) -> list[Decimal]:
    """Recomputed from `core/lmsr.py` rather than pinned as literals.

    The criterion is "prices come from `core/lmsr.py`", and
    `unit_test/core/test_lmsr.py` already pins the engine against an
    independent implementation — so a literal here would be a third
    restatement of the same number, and the one most likely to be pasted out
    of a failing run.

    `ROUND_HALF_UP`, with no direction to favour: nobody is charged a price.
    The directional rounding in `core/pricing.py::quantize_cost` is about a
    cost, and a snapshot quotes no cost.
    """
    return [p.quantize(_QUANTUM, rounding=ROUND_HALF_UP) for p in lmsr_prices(list(q), _B)]


# --- structural capture ---------------------------------------------------
@contextlib.contextmanager
def _capture_sql() -> Iterator[list[str]]:
    """Every statement the block sends to Postgres, in order.

    Copied from `test_preview.py` rather than shared, the same way
    `test_market_status.py` copies it. Three suites asserting structural facts
    about three different code paths is not duplication that wants removing:
    a shared helper would put the assertion machinery one import away from the
    file whose claims depend on it.
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
# What the snapshot returns
# =========================================================================
async def test_the_prices_are_the_lmsr_prices_of_the_books_q_and_b(
    session: AsyncSession,
) -> None:
    """The criterion: computed from `core/lmsr.py`, not estimated and not stored.

    ADR 0010's whole argument for putting this endpoint on the ledger is that
    the ledger owns `q`. A snapshot that returned a cached number, or a number
    off the last event this process happened to publish, would be the thing
    that record refused to build on `realtime_service`: an answer that cannot
    tell you it is empty.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    result = await _read(session, upstream)

    assert [p.price for p in result.prices] == _expected_prices(_Q)


async def test_the_prices_carry_every_outcome_in_position_order(
    session: AsyncSession,
) -> None:
    """Every outcome, not only a traded one, ordered by `position`.

    A trade moves the whole softmax, and a client that received one outcome
    would render a market whose prices no longer sum to one. The order is the
    one the administrator arranged at drafting, carried so a categorical market
    renders the same way twice without a second lookup — and it is `position`
    rather than insertion or id, which is why `_Q` is asymmetric.
    """
    upstream = _Upstream(outcomes=3)
    await _warm(session, upstream, q=[Decimal("10"), Decimal("40"), Decimal("25")])

    result = await _read(session, upstream)

    assert [p.position for p in result.prices] == [0, 1, 2]
    assert [p.outcome_id for p in result.prices] == upstream.outcomes


async def test_the_prices_are_quantized_to_scale_four(session: AsyncSession) -> None:
    """`ROUND_HALF_UP` at scale 4, by the same helper `service/preview.py` uses.

    The engine returns 50 significant digits and deliberately does not round —
    rounding there would let a caller round-trip a profit. Quantizing here is
    what makes a snapshot and a price frame the same string for the same state,
    which is the whole reason `docs/api/realtime-service.md` says a client
    renders both with one function.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    result = await _read(session, upstream)

    for entry in result.prices:
        assert entry.price.as_tuple().exponent == -4, entry.price


async def test_the_state_version_is_the_books_own(session: AsyncSession) -> None:
    """D-011: one counter per market, and the snapshot reports the same one the
    preview and the price events do.

    Written directly here, because nothing in this repository increments it —
    that is [T-2] #22, under the book row's lock. What matters is that the
    snapshot reads the column rather than deriving a number of its own: a
    client resumes its version check from this value, so a second counter would
    be a second answer to "has this market moved".
    """
    upstream = _Upstream()
    await _warm(session, upstream)
    book = _entities().MarketBook
    await session.execute(
        update(book).where(book.market_id == upstream.market_id).values(state_version=7)
    )
    await session.commit()

    result = await _read(session, upstream)

    assert result.state_version == 7


async def test_the_market_id_is_the_one_asked_for(session: AsyncSession) -> None:
    upstream = _Upstream()
    await _warm(session, upstream)

    assert (await _read(session, upstream)).market_id == upstream.market_id


# =========================================================================
# occurred_at
# =========================================================================
async def test_occurred_at_is_the_books_state_changed_at(
    session: AsyncSession,
) -> None:
    """The criterion, against a book whose state has moved since it opened.

    `state_changed_at` is written by whichever trade last moved `q`. Reading
    `opened_at` instead, or `datetime.now()`, would both pass on a never-traded
    market — which is why this test moves the column first and the one below
    checks the equal case separately.

    `now()` is the tempting wrong answer and the worst one: it reports the
    moment of the *read* as the moment of the event, so two clients opening the
    same unchanged market get two different `occurred_at` values for one state.
    """
    upstream = _Upstream()
    await _warm(session, upstream)
    moved = datetime(2026, 9, 20, 11, 30, tzinfo=UTC)
    book = _entities().MarketBook
    await session.execute(
        update(book)
        .where(book.market_id == upstream.market_id)
        .values(state_changed_at=moved, state_version=3)
    )
    await session.commit()

    result = await _read(session, upstream)

    assert result.occurred_at == moved


async def test_a_never_traded_markets_occurred_at_is_its_opened_at(
    session: AsyncSession,
) -> None:
    """D-029, read from the other end.

    The realtime contract defines `occurred_at` as the time of the event, and a
    market nobody has traded has had no event — so D-029 gives the field a
    defined value by making the book's creation its last state change. That is
    the truth rather than a convenience: `state_version` is 0, `q` is 0 for
    every outcome, and that state began when the row was written.

    Equality, not merely both-non-null, exactly as D-029's own tests assert it.
    """
    upstream = _Upstream()
    book = await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=_token(),
        transport=upstream.transport,
    )
    await session.commit()

    result = await _read(session, upstream)

    assert result.occurred_at == book.opened_at
    assert result.occurred_at == book.state_changed_at


async def test_occurred_at_is_timezone_aware(session: AsyncSession) -> None:
    """CLAUDE.md records that naive-versus-aware timestamps have already caused
    bugs here, and the consumer's own field says "timezone-aware, always UTC".
    A naive value serialises without an offset and a client parsing it gets
    local time — on the field the contract says is for display."""
    upstream = _Upstream()
    await _warm(session, upstream)

    assert (await _read(session, upstream)).occurred_at.tzinfo is not None


# =========================================================================
# The reads it makes
# =========================================================================
async def test_the_read_is_one_statement_joining_the_two_tables(
    session: AsyncSession,
) -> None:
    """D-013, the same statement `service/preview.py` already uses.

    Two facts, and the second catches a lazy load. Exactly one statement
    touches `market_books`, and that same statement also touches
    `market_outcomes` — so `q`, `b` and `state_version` come back under one
    snapshot. Under READ COMMITTED two statements fail in the dangerous
    direction: read `q` first and `state_version` second, and a trade
    committing between them hands a client a version newer than the `q` it is
    about to render, so its own version check then discards the correcting
    event as stale. The client is left showing a price that no frame will ever
    replace, which is precisely the failure `state_version` exists to prevent.

    Exactly one, not at most one, also pins the ordering: the join read comes
    first and `books.ensure_open` is the fallback when it returns nothing, not
    a preamble that checks for the book and then reads it again.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with _capture_sql() as statements:
        await _read(session, upstream)

    books = _mentioning(statements, "market_books")
    assert len(books) == 1, f"the warm path must read once, sent:\n{statements}"
    assert "market_outcomes" in books[0].lower(), (
        "`q` and `state_version` must come back from one statement (D-013), "
        f"and this one does not join them:\n{books[0]}"
    )


async def test_a_warm_snapshot_takes_no_locks(session: AsyncSession) -> None:
    """D-012: this read decides no write, so it takes no lock.

    ADR 0015's rule is about reads that decide a write, and it says reads
    feeding no write stay unlocked. A snapshot decides nothing at all — it
    renders.

    The cost of getting it wrong is contention rather than correctness, and it
    is worse here than on the preview. A preview serialises the traders typing
    into one market; a `FOR UPDATE` here serialises every *viewer* of a market,
    on the read that fires when a page opens and again on every reconnect —
    behind the trade that is holding the same row.

    This test fails when a lock is **added**, which is the reverse of every
    race test in this suite and the only honest way to assert an absence.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with _capture_sql() as statements:
        await _read(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking == [], f"the snapshot read must take no locks:\n{locking}"


async def test_a_warm_snapshot_writes_nothing(session: AsyncSession) -> None:
    """The fact the other two do not cover.

    A statement count and a lock check both pass against an implementation that
    prices correctly and then writes something on the way out — a cached price,
    a `last_snapshot_at`, a view counter. This is a `GET`, and the only write
    it is allowed to perform is the first touch that opens a book.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    with _capture_sql() as statements:
        await _read(session, upstream)

    assert _writes(statements) == [], (
        f"a warm snapshot must not write:\n{_writes(statements)}"
    )


async def test_a_warm_snapshot_changes_nothing(session: AsyncSession) -> None:
    """The same claim as state rather than as statements.

    Asserted alongside the capture rather than instead of it, because the two
    fail differently: a write through a path the capture does not see shows up
    here, and a write that happens to restore the same values shows up there.
    """
    upstream = _Upstream()
    await _warm(session, upstream)
    before = await _book_row(session, upstream)
    snapshot = (before.state_version, before.state_changed_at, before.liquidity_b)
    entries_before = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()

    await _read(session, upstream)

    after = await _book_row(session, upstream)
    assert (after.state_version, after.state_changed_at, after.liquidity_b) == snapshot
    assert (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one() == entries_before


# =========================================================================
# The cold path — a second first-toucher
# =========================================================================
async def test_a_market_with_no_book_has_one_opened(session: AsyncSession) -> None:
    """The criterion, and the consequence D-037 did not anticipate by name.

    D-037 made the preview a market's first toucher because refusing a cold
    market would show a trader an error where a price belongs. The same
    argument lands harder here: `docs/api/realtime-service.md`'s "Opening a
    page" sequence puts the snapshot *first*, before the socket is even open,
    so on a market nobody has traded the snapshot is reliably the request that
    pays the terms timeout — and refusing would break the one read [X-4] #37's
    reconnect depends on.
    """
    upstream = _Upstream()

    result = await _read(session, upstream)

    assert upstream.calls == 1
    assert (await _book_row(session, upstream)).market_id == upstream.market_id
    assert len(result.prices) == 2


async def test_a_cold_market_prices_at_zero_shares(session: AsyncSession) -> None:
    """The book opens with `q = 0` everywhere, so a two-outcome market opens at
    an even split. Asserted because "it returned a number" is not the same as
    "it returned the right one", and this is the number a client renders on the
    very first page open a market ever gets."""
    upstream = _Upstream()

    result = await _read(session, upstream)

    assert [p.price for p in result.prices] == _expected_prices(
        [Decimal(0), Decimal(0)]
    )


async def test_the_callers_own_token_is_forwarded_upstream(
    session: AsyncSession,
) -> None:
    """D-018's Notes: the ledger forwards the caller's credential and mints
    none of its own. A token minted here would be this service asserting an
    identity it was not given, on a read it makes on somebody's behalf."""
    upstream = _Upstream()
    token = mint_token(uuid.uuid4())

    await _read(session, upstream, access_token=token)

    assert upstream.tokens == [token]


async def test_a_second_snapshot_makes_no_http_call_at_all(
    session: AsyncSession,
) -> None:
    """The cold path is self-extinguishing, and here that matters more than it
    did for the preview.

    A snapshot fires on every page open and on every reconnect. If it re-read
    the terms each time, `market_service` would become an availability
    dependency of *rendering a market*, and a reconnect storm against the
    realtime service would turn into a load test of the market service.
    """
    upstream = _Upstream()
    await _read(session, upstream)
    upstream.calls = 0

    await _read(session, upstream)

    assert upstream.calls == 0


async def test_the_cold_path_takes_the_handoffs_locks(session: AsyncSession) -> None:
    """D-036, the other half of `test_a_warm_snapshot_takes_no_locks`.

    "No lock" is the *pricing read*, and this ticket's criteria cite D-012
    without D-036's correction. The path that opens a book is a write like any
    other and ADR 0015 applies to it normally: `books.ensure_open` ends in
    `posting.post`, and `accounts.lock` holds the `PLATFORM` row and the
    market's pool row `FOR UPDATE` while the funding transaction commits.
    Removing those to satisfy the sentence "no lock" would reintroduce D-010's
    first-touch race on the one write this route performs.
    """
    upstream = _Upstream()

    with _capture_sql() as statements:
        await _read(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking != [], (
        "the first touch funds a pool and must take the handoff's locks "
        "(D-036); only the pricing read is unlocked"
    )


# =========================================================================
# It never gates on status
# =========================================================================
async def test_a_closed_market_is_still_priced(session: AsyncSession) -> None:
    """ADR 0017, verbatim: "the realtime snapshot serves a closed market's
    prices".

    A closed market still has a price to render — the last one anybody traded
    at — and a client that has it on screen when the market closes must not
    have it replaced by an error on its next reconnect. The market's own page
    gates its trade controls on `#62`'s derived status; this route's job is to
    answer what the prices are.
    """
    upstream = _Upstream(status="closed")
    await _warm(session, upstream)

    result = await _read(session, upstream)

    assert [p.price for p in result.prices] == _expected_prices(_Q)


async def test_a_closed_market_makes_no_status_hop(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is absent, not merely lenient.

    Two assertions, because an implementation can fail this two ways. It can
    call `market_status.ensure_trading` and ignore what it raises, which the
    call count catches. Or it can make ADR 0017's HTTP hop and then not act on
    the answer, which is worse than the gate itself — every page open and every
    reconnect becomes a round trip to market_service, on a warm market that
    needs nothing from it.

    `ensure_trading` is replaced with something that raises rather than
    counted, so a call fails this test wherever it is made and whatever the
    caller does with the result.
    """
    upstream = _Upstream(status="closed")
    await _warm(session, upstream)

    from service import market_status  # noqa: PLC0415

    async def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "the snapshot must not gate on status (ADR 0017): a closed "
            "market still has a price to render"
        )

    monkeypatch.setattr(market_status, "ensure_trading", forbidden)

    await _read(session, upstream)

    assert upstream.calls == 0, (
        "a warm snapshot made an HTTP call to market_service; ADR 0017's hop "
        "belongs to the trade path, which fires once per trade, not to a read "
        "that fires on every page open and every reconnect"
    )


async def test_a_closed_market_with_no_book_still_gets_one(
    session: AsyncSession,
) -> None:
    """The two rules meeting, which is where an implementation is most likely
    to hedge.

    A cold *closed* market is the case that looks wrong and is not: the book is
    opened, the pool is funded from `PLATFORM`, and the market will never
    trade. D-037 accepted exactly this — settlement returns whatever the pool
    has left to `PLATFORM` under [3.4] #12, so a dead pool overstates credits
    in circulation until its market resolves rather than permanently — and it
    is the price of the cold path not having to learn a status it cannot read.
    """
    upstream = _Upstream(status="closed")

    result = await _read(session, upstream)

    assert upstream.calls == 1
    assert len(result.prices) == 2


@pytest.mark.parametrize("status", ["closed", "pending_resolution", "approved"])
async def test_no_status_is_refused(session: AsyncSession, status: str) -> None:
    """Every status market_service can report, not just `closed`.

    ADR 0017 notes that PENDING_RESOLUTION and APPROVED "pass through as
    themselves and are refused by the same comparison" on the trade path. Here
    none of them is refused: a settled market still has a last price, and a
    market awaiting a decision is the one people most want to look at.
    """
    upstream = _Upstream(status=status)

    result = await _read(session, upstream)

    assert len(result.prices) == 2


# =========================================================================
# Failures, reusing the preview's codes
# =========================================================================
async def test_a_market_that_does_not_exist_raises_market_not_found(
    session: AsyncSession,
) -> None:
    """404 `market_not_found`, from the cold path's terms pull.

    Deliberately not distinguished from a draft or a submitted market:
    `browsing.get_published` on the other side makes those three
    indistinguishable on purpose, so this side inherits the ambiguity rather
    than inventing a distinction it cannot support.
    """
    upstream = _Upstream()

    with pytest.raises(_errors().MarketNotFound):
        await _read(session, upstream, transport=upstream.missing)


async def test_an_unpublished_market_raises_market_not_published(
    session: AsyncSession,
) -> None:
    """409 `market_not_published`. A market with no `published_at` has no terms
    to open a book from, and nothing to price."""
    upstream = _Upstream(published_at=None)

    with pytest.raises(_errors().MarketNotPublished):
        await _read(session, upstream)


async def test_an_unreachable_market_service_raises_market_terms_unavailable(
    session: AsyncSession,
) -> None:
    """503 `market_terms_unavailable`, and only on a market's first touch.

    The request was fine and the dependency was not, which is what separates
    this from the two above: retrying in a moment helps, and after one success
    it can never fire for this market again.
    """
    upstream = _Upstream()

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _read(session, upstream, transport=upstream.dead)


async def test_an_unreachable_market_service_cannot_stop_a_warm_snapshot(
    session: AsyncSession,
) -> None:
    """The fast path holds when market_service is down.

    This is what makes the cold path's cost acceptable, and it is worth an
    assertion of its own: once a market has a book, rendering its price depends
    on Postgres and nothing else. A market_service outage stops new markets
    being opened; it does not blank every price on every screen.
    """
    upstream = _Upstream()
    await _warm(session, upstream)

    result = await _read(session, upstream, transport=upstream.dead)

    assert [p.price for p in result.prices] == _expected_prices(_Q)


async def test_a_refused_market_leaves_no_book_behind(session: AsyncSession) -> None:
    """Nothing is committed on the way to a refusal.

    `books.ensure_open` orders every write through `session.begin_nested()` and
    commits only inside `posting.post`, called last (D-032) — so a raise before
    that point leaves the pool account, the book and its outcomes all unwritten.
    Asserted from this route because a second first-toucher is a second caller
    that could get the ordering wrong.
    """
    upstream = _Upstream(published_at=None)
    book = _entities().MarketBook

    with pytest.raises(_errors().MarketNotPublished):
        await _read(session, upstream)

    await session.rollback()
    assert (
        await session.execute(
            select(func.count()).select_from(book).where(book.market_id == upstream.market_id)
        )
    ).scalar_one() == 0
