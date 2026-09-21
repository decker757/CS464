"""The stopped-market gate: may this market still be traded? [F-8] #109, ADR 0017.

`ledger.market_books` carries no status and no `close_time`, and ADR 0017
works through why neither can be added: ADR 0011 makes the clock the authority
over two values in `market.markets`, and ADR 0014 leaves `close_time` in the
future on an early close on purpose, so a snapshot of it would accept trades
against a market an administrator deliberately stopped. The answer is a read
of market_service's public detail endpoint, whose `status` is already ADR
0011's predicate applied — one field answering both the clock's close and an
administrator's.

**There is no trade path, and nothing here invents one.** [T-2] #22 is the
trade. What this file drives is the primitive #22 will call,
`service/market_status.py::ensure_trading`, exactly as `test_market_books.py`
drove `books.ensure_open` before either of its callers existed. The ordering
criteria — replay before the gate, the gate before the book lock — moved to
#22 with the code that can be tested against them. Nothing below asserts an
order that needs a trade to exist.

**What the gate is, and what it is not.** It raises or it returns nothing. It
does not hand back `MarketTerms`: a trade holding fresh terms would be one
refactor away from pricing off a wire `liquidity_b` instead of the book's
immutable snapshot, which is the exact bug
`test_a_null_liquidity_b_does_not_refuse_the_gate_on_a_warm_book` below exists
to prevent.

**Why it takes a session.** One upstream 404 has two correct answers, and
`ledger.market_books` is what selects between them. That read happens on the
404 branch only — `test_an_open_gate_sends_nothing_to_postgres` is the
assertion that keeps it there — and it takes no lock, because ADR 0015 governs
reads that decide a *write* and this one decides an error code.

The upstream is mocked at the transport, so these tests exercise the real
request building, the real headers and the real parsing.
`test_market_terms.py` owns the wire format; this file owns the decision taken
on it. Driving the real market service over `ASGITransport` is not an option:
`unit_test/test_import_boundary.py` fails any `import market_service` from
here, because that import succeeds under pytest and is an `ImportError` in the
container.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine
from model.entities import Account, AccountKind, Entry, Transaction
from unit_test.conftest import mint_token


def _status():
    """Imported inside each test rather than at module scope.

    `service/market_status.py` does not exist yet, and a top-level import of it
    would be one collection error that takes the whole file down as a single
    red line. Reached through here, every test below fails on its own, named
    after the criterion it is holding — which is the point of writing them
    first (D-007). Same convention as `test_market_terms.py`.
    """
    from service import market_status  # noqa: PLC0415

    return market_status


def _books():
    """`service/books.py` exists; this file only ever reads through it."""
    from service import books  # noqa: PLC0415

    return books


def _errors():
    """`core/errors.py` exists; `MarketClosed` does not yet."""
    from core import errors  # noqa: PLC0415

    return errors


def _entities():
    """`model/entities.py` exists, `MarketBook` and all."""
    from model import entities  # noqa: PLC0415

    return entities


_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")

# Far enough out that no test run drifts past it, and the same value
# `test_market_terms.py` and `test_market_books.py` already use.
_FUTURE = "2027-01-05T12:00:00Z"


class _Upstream:
    """A stand-in market service whose answer can change between calls.

    Mutable on purpose. Two criteria need the market to close *between* the
    book's creation and the gate call, which is the shape an early close has
    in production: a book that has existed and priced for weeks, and a market
    an administrator stopped this morning. A fixture that could only answer one
    way could not express it.

    The call count and the tokens are both load-bearing below — one hop per
    gate, carrying the caller's own credential and nothing minted here.
    """

    def __init__(
        self,
        *,
        status: str | object = "open",
        close_time: str | None = _FUTURE,
        liquidity_b: Decimal | str | None = _B,
        seed_subsidy: Decimal | str | None = _SUBSIDY,
        published_at: str | None = "2026-09-01T09:00:00Z",
        outcomes: int = 2,
        status_code: int = 200,
        drop_status: bool = False,
    ) -> None:
        self.calls = 0
        self.tokens: list[str] = []
        self.requests: list[httpx.Request] = []
        self.status_code = status_code
        self.raises: Exception | None = None
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]
        self.body: dict[str, object] = {
            "id": str(self.market_id),
            "status": status,
            "close_time": close_time,
            "resolution_time": "2027-01-20T12:00:00Z",
            "liquidity_b": None if liquidity_b is None else str(liquidity_b),
            "seed_subsidy": None if seed_subsidy is None else str(seed_subsidy),
            "published_at": published_at,
            "outcomes": [
                {"id": str(o), "position": i, "label": f"Outcome {i}"}
                for i, o in enumerate(self.outcomes)
            ],
        }
        if drop_status:
            del self.body["status"]

    def closes(
        self, *, status: str = "closed", close_time: str | None = None
    ) -> None:
        """What the market service starts saying after the market stops.

        `close_time` is left alone by default, which is ADR 0014's early close
        exactly: the status moves, `closed_at` is written, and the published
        closing time stays in the future because it is a term traders read.
        """
        self.body["status"] = status
        if close_time is not None:
            self.body["close_time"] = close_time

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            self.requests.append(request)
            self.tokens.append(
                request.headers.get("Authorization", "").removeprefix("Bearer ")
            )
            if self.raises is not None:
                raise self.raises
            return httpx.Response(self.status_code, json=self.body)

        return httpx.MockTransport(handler)


def _token() -> str:
    return mint_token(uuid.uuid4())


async def _gate(
    session: AsyncSession, upstream: _Upstream, *, access_token: str | None = None
) -> None:
    """One gate call, with the confirmed signature."""
    return await _status().ensure_trading(
        session,
        upstream.market_id,
        access_token=access_token if access_token is not None else _token(),
        transport=upstream.transport,
    )


async def _warm(session: AsyncSession, upstream: _Upstream):
    """A market whose book exists and is committed.

    `books.ensure_open` ends in `posting.post`, which commits, so by the time
    this returns the book is durable and a later gate call is looking at the
    state a market weeks into trading would be in. The upstream's answer is
    then free to change, which is what the early-close tests need.
    """
    book = await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=_token(),
        transport=upstream.transport,
    )
    upstream.calls = 0
    upstream.tokens.clear()
    upstream.requests.clear()
    return book


async def _book_row(session: AsyncSession, market_id: uuid.UUID):
    book = _entities().MarketBook
    session.expire_all()
    return (
        await session.execute(select(book).where(book.market_id == market_id))
    ).scalar_one_or_none()


@contextmanager
def _capture_sql() -> Iterator[list[str]]:
    """Every statement the block sends to Postgres, in order.

    Attached to the engine rather than inferred from timings, for the reason
    `test_preview.py` records: a structural claim deserves a structural
    assertion, and `before_cursor_execute` sees the compiled statement text.
    That is what makes "no `FOR UPDATE`, no write, and on the open path no
    statement at all" checkable without pinning any SQL syntax.
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


def _writes(statements: Sequence[str]) -> list[str]:
    return [
        s
        for s in statements
        if s.lstrip().lower().startswith(("insert", "update", "delete"))
    ]


# =========================================================================
# The gate: which markets are refused
# =========================================================================
@pytest.mark.parametrize("status", ["closed", "pending_resolution", "approved"])
async def test_a_market_that_is_not_open_is_refused(
    session: AsyncSession, status: str
) -> None:
    """The criterion's own three cases. ADR 0017's `409 market_closed`.

    `closed` is both kinds of close at once — the clock's, derived by #62
    before the sweep has written it, and an administrator's under [2.3] #7,
    which the derivation never reopens. `pending_resolution` and `approved`
    pass through the public projection as themselves, and both mean a market
    whose outcome is being decided: a trade against either is a bet placed
    after the result is known to somebody, which is the harm
    `service/validation.py`'s `close_time < resolution_time` rule exists to
    prevent.
    """
    upstream = _Upstream(status=status)

    with pytest.raises(_errors().MarketClosed):
        await _gate(session, upstream)


@pytest.mark.parametrize(
    "status", ["settled", "draft", "submitted", "halted"]
)
async def test_a_status_nobody_enumerated_is_refused_too(
    session: AsyncSession, status: str
) -> None:
    """"One comparison, not a list of statuses" — the only testable form of it.

    A test cannot see the source, and `status != "open"` and
    `status in {"closed", "pending_resolution", "approved"}` agree on every
    input the criterion above names. They disagree here, which is what makes
    this the test that actually holds the rule.

    **`settled` is the case with a date on it.** `MarketStatus` says "SETTLED
    with [3.4] #12", so the member arrives the day settlement lands. A
    list-based gate would start accepting trades against settled markets that
    morning, paying out against a book whose winner has already been decided
    and whose positions have already been settled, with nothing anywhere going
    red. `draft` and `submitted` are unreachable through `get_published`, which
    404s them, and `halted` stands for every member added after this code is
    written by somebody who never reads it.
    """
    upstream = _Upstream(status=status)

    with pytest.raises(_errors().MarketClosed):
        await _gate(session, upstream)


async def test_an_open_market_is_allowed_through(session: AsyncSession) -> None:
    """The main flow, and the thing every refusal above is measured against.

    Returns `None` rather than the terms. A gate that handed back
    `MarketTerms` would put a fresh `liquidity_b` in the trade path's hand,
    one refactor away from being priced off instead of the book's snapshot —
    and ADR 0005 makes that snapshot the authority precisely because it cannot
    change.
    """
    upstream = _Upstream(status="open")

    assert await _gate(session, upstream) is None


async def test_a_market_closed_early_with_a_future_close_time_is_refused(
    session: AsyncSession,
) -> None:
    """[2.3] #7, and the half of the predicate no local column can answer.

    ADR 0014 leaves `close_time` in the future on an early close on purpose —
    it is "a term the administrator published and traders read", and a
    `closed_at` earlier than `close_time` is the only signal that a market was
    stopped by hand. So a ledger comparing a snapshotted `close_time` against
    its own clock would accept every trade on this market until its original
    closing time, which is [2.3] #7's whole purpose reintroduced one service
    downstream of it.

    Driven with the close committed **between** the book's creation and the
    gate call, because that is the shape in production: a book that has priced
    correctly for weeks, and a market stopped this morning. `books.ensure_open`
    ends in `posting.post`, which commits, so the book below is durable before
    the upstream changes its answer.
    """
    upstream = _Upstream(status="open", close_time=_FUTURE)
    await _warm(session, upstream)
    assert await _book_row(session, upstream.market_id) is not None

    upstream.closes()  # status moves, close_time deliberately left in the future

    assert upstream.body["close_time"] == _FUTURE, (
        "an early close must not move close_time; ADR 0014 is the record"
    )
    with pytest.raises(_errors().MarketClosed):
        await _gate(session, upstream)


async def test_a_market_past_its_close_time_is_refused_before_the_sweep_has_run(
    session: AsyncSession,
) -> None:
    """ADR 0011's window, as the ledger actually meets it.

    The sweep is market_service's and writes `CLOSED` a few seconds after the
    clock passes. For those seconds the raw column still says `open` — and the
    public projection does not, because #62 derives it (D-022, D-027). The
    body below is exactly what that endpoint returns in the window:
    `status: "closed"`, a `close_time` already past, and no `closed_at`
    anywhere, because `PublicMarketOut` carries no such field.

    **No flag is toggled and nothing waits.** `CLOSE_SWEEP_ENABLED` is
    market_service's setting and `test_import_boundary.py` forbids this suite
    from reaching for it. The upstream half is pinned next door by
    `test_a_market_past_its_close_time_is_reported_closed`, which moves
    `close_time` back and deliberately never runs the sweeper. What this owns
    is the other half: given that body, the ledger refuses.
    """
    stopped = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    upstream = _Upstream(status="closed", close_time=stopped)

    assert "closed_at" not in upstream.body, (
        "PublicMarketOut carries no closed_at; a fixture with one would be "
        "testing against an endpoint that does not exist"
    )

    with pytest.raises(_errors().MarketClosed):
        await _gate(session, upstream)


async def test_a_close_time_already_past_does_not_refuse_an_open_market(
    session: AsyncSession,
) -> None:
    """The complement, and what makes the sweep irrelevant by construction.

    ADR 0017: the gate "reads that projection rather than restating it, which
    is ADR 0011's own argument applied across a service boundary." There is
    exactly one predicate in this system and it lives in
    `market_service/core/closing.py`. A ledger that also compared `close_time`
    against its own clock would be the second copy, in a second process, with
    a second clock — and ADR 0017 declines the one-clock-per-request trigger
    precisely because the ledger's transaction cannot supply the clock for a
    comparison market_service performs.

    So this body — `status: "open"` beside a `close_time` an hour past — is
    unreachable through a correct market service, and the honest answer to it
    is to trade. Refusing here would mean the ledger had opinions about a
    predicate it does not own, and the first symptom would be trades refused
    during a clock skew between two hosts.

    This is also the test that fails if somebody "fixes" the sweep window by
    adding a local `close_time` comparison, which is the shape ADR 0011
    rejected and ADR 0017 rejected again.
    """
    stopped = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    upstream = _Upstream(status="open", close_time=stopped)

    assert await _gate(session, upstream) is None


# =========================================================================
# The regression the refactor exists to prevent
# =========================================================================
async def test_a_null_liquidity_b_does_not_refuse_the_gate_on_a_warm_book(
    session: AsyncSession,
) -> None:
    """ADR 0017's reason for moving the terms rules out of `_parse`.

    Today `_parse` refuses a null `liquidity_b` as `MarketTermsUnavailable`.
    That is the right rule for *writing a book* — a `b` that can never be
    priced, snapshotted immutably under ADR 0005 — and the wrong one for
    asking whether a market is open. A market_service that began returning a
    null `b` would, with the rule left where it is, start refusing trades on
    books that have priced correctly for weeks, against a value this service
    read once at first touch and never reads again.

    The book below is opened from a good body and committed. The upstream then
    starts answering with a null `b`, and the gate must not care: it reads
    `status` and nothing else. `b` comes from `ledger.market_books`, where it
    has been since the first touch.

    This is the one test in the file that fails for a reason other than
    `MarketClosed` not existing — it fails on the unmoved refusal, which is
    what makes it the refactor's evidence.
    """
    upstream = _Upstream(status="open")
    await _warm(session, upstream)

    upstream.body["liquidity_b"] = None

    assert await _gate(session, upstream) is None


async def test_an_unpriceable_outcome_list_does_not_refuse_the_gate_either(
    session: AsyncSession,
) -> None:
    """`_refuse_unpriceable` moves for the same reason, and this is its half.

    A single outcome, or none, is a market that can never be priced — and the
    book that would be written from it is the thing worth refusing, which is
    why the rule follows the null-`b` rule into `books.ensure_open` rather
    than staying on a read that decides whether trading is open.

    A market with a book already has an outcome list that passed that rule
    once, immutably. Refusing the gate on what the wire says today would stop
    trading on a market whose stored outcomes are fine.
    """
    upstream = _Upstream(status="open")
    await _warm(session, upstream)

    upstream.body["outcomes"] = [
        {"id": str(upstream.outcomes[0]), "position": 0, "label": "Yes"}
    ]

    assert await _gate(session, upstream) is None


# =========================================================================
# What the gate touches: no lock, no write, and no query on the open path
# =========================================================================
async def test_an_open_gate_sends_nothing_to_postgres(
    session: AsyncSession,
) -> None:
    """The session is for the 404 branch only, and this keeps it there.

    The obvious implementation reads the book up front and then decides. That
    passes every other test in this file and costs an indexed read on the
    hottest path in the system, on every trade, forever — for a value needed
    only when market_service answers 404, which is a case that should never
    happen at all.

    Asserted as "no statement", not "no statement mentioning `market_books`",
    because there is nothing else the gate could legitimately be asking.
    """
    upstream = _Upstream(status="open")

    with _capture_sql() as statements:
        await _gate(session, upstream)

    assert statements == [], (
        "the gate's only job on an open market is one HTTP call; it sent "
        f"{len(statements)} statement(s) to Postgres:\n" + "\n".join(statements)
    )


async def test_a_refused_gate_writes_nothing(session: AsyncSession) -> None:
    """"Before anything is written", stated as state rather than as an order.

    The ordering half — that the gate runs before the book lock and before any
    leg is posted — is [T-2] #22's, because it needs a trade to order against.
    What this holds is the part that is true of the gate standing alone: a
    refusal leaves the database exactly as untouched as it was, so nothing
    downstream can find a half-opened book and take the fast path through it.
    """
    upstream = _Upstream(status="closed")

    with pytest.raises(_errors().MarketClosed):
        await _gate(session, upstream)

    await session.rollback()

    assert await _book_row(session, upstream.market_id) is None
    for table in (Entry, Transaction, Account):
        assert (
            await session.execute(select(func.count()).select_from(table))
        ).scalar_one() == 0, f"a refused gate wrote to {table.__tablename__}"


async def test_the_404_branch_read_is_unlocked(session: AsyncSession) -> None:
    """ADR 0015 governs reads that decide a write. This one decides a code.

    The rule is "every read whose answer decides a write is
    `with_for_update()`". The book read here decides between `503` and `404`
    and writes nothing at all — and there is nothing a lock could hold still
    anyway, since the answer it is qualifying came from another service's
    database half a round trip ago. ADR 0017 makes the same argument for why
    the whole gate sits outside the book lock.

    Taking one here would be free correctness theatre with a real cost: the
    hottest row in the system, held on a path that exists only for a market
    service answering incorrectly.
    """
    upstream = _Upstream(status="open")
    await _warm(session, upstream)
    upstream.status_code = 404
    upstream.body = {"code": "market_not_found"}

    with _capture_sql() as statements:
        with pytest.raises(_errors().MarketTermsUnavailable):
            await _gate(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking == [], (
        "the 404 branch reads the book to choose an error code, which decides "
        f"no write and takes no lock:\n" + "\n".join(locking)
    )
    assert _writes(statements) == [], (
        "the 404 branch is a read:\n" + "\n".join(_writes(statements))
    )


# =========================================================================
# The hop, and the credential it carries
# =========================================================================
async def test_the_gate_asks_the_public_detail_endpoint_once(
    session: AsyncSession,
) -> None:
    """One hop per gate, at `GET /public/markets/{id}`. ADR 0005's budget.

    The admin route `/markets/{id}` is scoped to the creator and requires an
    admin, so a gate pointed at it would 403 every trader in the system — and
    the one administrator it worked for would be reading `MarketOut`, whose
    `status` is the raw column rather than the derived one, which is the whole
    field this gate exists to read (D-022).

    One call, not two. ADR 0017 charges market_service's availability against
    every trade here; charging it twice would be that cost doubled for nothing.
    """
    upstream = _Upstream(status="open")

    await _gate(session, upstream)

    assert upstream.calls == 1
    assert upstream.requests[0].method == "GET"
    assert upstream.requests[0].url.path == f"/public/markets/{upstream.market_id}"


async def test_the_caller_s_own_token_is_forwarded_and_none_is_minted(
    session: AsyncSession,
) -> None:
    """D-018's Notes, now describing every trade rather than one read per market.

    The ledger has no minting code and must never grow any: a token minted here
    would be this service asserting an identity it was not given, on a call
    that reaches another service — the service-to-service auth question ADR
    0009 deferred, arriving through the back door of a read.

    ADR 0017 records what this makes load-bearing: the warning that "a pull
    with no caller behind it — a retry, a sweep, any background path — has no
    token to forward" described a once-per-market read when it was written and
    now describes the hot path. [T-7] #27 is the ticket that meets it.
    """
    upstream = _Upstream(status="open")
    token = _token()

    await _gate(session, upstream, access_token=token)

    assert upstream.tokens == [token]
    assert upstream.requests[0].headers["Authorization"] == f"Bearer {token}"


# =========================================================================
# Errors: the dependency failing must never read as a closed market
# =========================================================================
async def test_an_unreachable_market_service_is_unavailable(
    session: AsyncSession,
) -> None:
    """503, and specifically not 409. ADR 0017's "it fails closed".

    An outage refuses the trade rather than letting it run on a stale answer,
    which is the half of ADR 0011's argument that survives the boundary: an
    outage does not widen the window here, it closes it. But the code has to
    say *dependency*, because `market_closed` is permanent and this is worth
    retrying in a moment — and a trader told their market has closed when it
    has not is a trader who stops trying.
    """
    upstream = _Upstream(status="open")
    upstream.raises = httpx.ConnectError("market service is down")

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


async def test_a_timeout_is_unavailable(session: AsyncSession) -> None:
    """The market service accepted the connection and then stopped talking.

    Same answer as a refused connection, deliberately: from this side they are
    the same event. D-030 bounds every phase at five seconds, which matters
    more here than on the cold path — ADR 0017 keeps this call outside the
    book lock, but the request still holds a session while it waits.
    """
    upstream = _Upstream(status="open")
    upstream.raises = httpx.ReadTimeout("slow")

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_an_upstream_server_error_is_unavailable(
    session: AsyncSession, status_code: int
) -> None:
    """A 5xx is the market service saying it could not answer.

    Parametrised across the three a deployment produces: the service itself
    failing, and the two a proxy in front of it produces while it restarts.
    """
    upstream = _Upstream(status="open", status_code=status_code)
    upstream.body = {"detail": "boom"}

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("html from a proxy", b"<html>not json</html>"),
        ("a JSON array", b"[]"),
        ("a JSON string", b'"not a market"'),
    ],
)
async def test_an_unparseable_200_is_unavailable(
    session: AsyncSession, label: str, body: bytes
) -> None:
    """A 200 carrying something that is not a market is the dependency, not a close.

    The shape this produces in practice is a proxy or a login page answering
    200 with HTML. `json.loads` succeeding says the bytes parsed, not that this
    is a market — and neither failure may reach the caller as `409`, because
    every one of them means the thing on the other end is not market_service.
    """
    upstream = _Upstream(status="open")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _status().ensure_trading(
            session,
            upstream.market_id,
            access_token=_token(),
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("the field is absent", {"drop_status": True}),
        ("the field is null", {"status": None}),
        ("the field is a number", {"status": 3}),
        ("the field is a list", {"status": ["open"]}),
        ("the field is an object", {"status": {"value": "open"}}),
    ],
)
async def test_a_status_that_is_not_a_string_is_unavailable_not_closed(
    session: AsyncSession, label: str, kwargs: dict[str, object]
) -> None:
    """The one that must not be got wrong, and the reason it has its own test.

    Every case here is `!= "open"`, so the naive comparison refuses all five as
    `409 market_closed` — silently, with no exception anywhere, exactly the way
    `Decimal(True)` used to open a book at `b = 1`. What it would mean in
    production is a market_service that had broken or been misconfigured
    telling every trader on the platform that every market had closed, in a
    system where a close is permanent and nothing reopens a market.

    ADR 0017 keeps "a parseable status" among the structural rules `_parse`
    holds, alongside an object and a matching id. A sick dependency is a 503,
    and 503 is the code that says retrying is worth it.
    """
    upstream = _Upstream(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


async def test_a_404_on_a_market_with_a_book_is_unavailable_not_not_found(
    session: AsyncSession,
) -> None:
    """The row in ADR 0017's table that looks wrong and is not.

    A market holding a book existed and was published — `books.ensure_open`
    refuses anything else — and nothing in this repository deletes a market.
    So a 404 at this point is market_service answering incorrectly, not a
    market that is gone, and answering 404 would tell a trader that the market
    they are looking at does not exist.

    503 says there is no usable answer and fails closed, which is the same
    thing every other unusable answer from that service says.
    """
    upstream = _Upstream(status="open")
    await _warm(session, upstream)
    assert await _book_row(session, upstream.market_id) is not None

    upstream.status_code = 404
    upstream.body = {"code": "market_not_found"}

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


async def test_a_404_on_a_market_with_no_book_is_not_found(
    session: AsyncSession,
) -> None:
    """The other branch, and the reason the gate needs a session at all.

    Both are reachable on the live trade path. On a cold market the gate runs
    before `books.ensure_open`, so a market with no book is the ordinary state
    of a first trade — and a 404 there means what it has always meant:
    no such market, a draft, or a submitted one, made indistinguishable on
    purpose by `browsing.get_published` so that nothing leaks what
    administrators are half-writing.

    All three are permanent, which is what separates this from the test above.
    Answering 503 would tell a trader to retry a market that is never going to
    appear, and would hide a trade against an id that does not exist inside a
    message about the market service being down. ADR 0017: "On the cold path
    the existing 404 to `MarketNotFound` mapping is untouched."
    """
    upstream = _Upstream(status="open", status_code=404)
    upstream.body = {"code": "market_not_found"}

    assert await _book_row(session, upstream.market_id) is None

    with pytest.raises(_errors().MarketNotFound):
        await _gate(session, upstream)


async def test_an_upstream_401_stays_not_authenticated(
    session: AsyncSession,
) -> None:
    """The forwarded token was rejected, which is a fact about the caller.

    A trader's access token lives fifteen minutes and ADR 0001's logout cannot
    revoke one early, so the honest answer is the one that tells them to log in
    again — not one that blames a dependency that is working perfectly, and
    certainly not one that says the market has closed.

    `NotAuthenticated` already exists at 401 and is reused, so this arrives at
    the client as the same shape as any other expired session.
    """
    upstream = _Upstream(status="open", status_code=401)
    upstream.body = {"code": "invalid_token"}

    with pytest.raises(_errors().NotAuthenticated):
        await _gate(session, upstream)
