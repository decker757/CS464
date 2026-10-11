"""The stopped-market gate: may this market still be traded? [F-8] #109, ADR 0017.

`ensure_trading` asks market_service, whose derived `status` answers both the
clock's close and an administrator's. It raises or returns nothing, never
terms. Its session is read only on a 404, unlocked. The ordering against a
trade (replay, gate, book lock) is the trade path's to test.
`test_market_terms.py` owns the wire format; this file owns the decision.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine, get_session_factory
from model.entities import Account, Entry, Transaction
from unit_test.book_fixtures import Stalled
from unit_test.conftest import idle_in_transaction, mint_token, terms_client_over


def _status():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import market_status  # noqa: PLC0415

    return market_status


def _books():
    """`service/books.py`, reached lazily like `_status`."""
    from service import books  # noqa: PLC0415

    return books


def _errors():
    """`core/errors.py`, reached lazily like `_status`."""
    from core import errors  # noqa: PLC0415

    return errors


def _entities():
    """`model/entities.py`, reached lazily like `_status`."""
    from model import entities  # noqa: PLC0415

    return entities


_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")

# Far enough out that no test run drifts past it, and the same value
# `test_market_terms.py` and `test_market_books.py` already use.
_FUTURE = "2027-01-05T12:00:00Z"


class _Upstream:
    """A stand-in market service whose answer can change between calls, so a
    market can close after its book was opened. Records calls and tokens.
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
            "proposed_outcome_id": None,
            "settleable": False,
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
        """What the market service says after the market stops. `close_time`
        is left alone by default: ADR 0014's early close."""
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


_BACKEND = pathlib.Path(__file__).resolve().parents[3]


async def _gate(
    session: AsyncSession, upstream: _Upstream, *, access_token: str | None = None
) -> None:
    """One gate call, with the confirmed signature."""
    return await _status().ensure_trading(
        session,
        upstream.market_id,
        access_token=access_token if access_token is not None else _token(),
        terms_client=terms_client_over(upstream.transport),
    )


async def _warm(session: AsyncSession, upstream: _Upstream):
    """A market whose book exists and is committed (`ensure_open` commits),
    with the upstream's counters reset."""
    book = await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=_token(),
        terms_client=terms_client_over(upstream.transport),
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


async def _written(session: AsyncSession, market_id: uuid.UUID) -> list[str]:
    """The tables holding a row this gate could have written, seen from
    `session`. Empty means nothing.

    `session.new` is checked explicitly: with `autoflush=False` the queries
    below cannot see a pending add.
    """
    written = [type(obj).__tablename__ for obj in session.new]
    if await _book_row(session, market_id) is not None:
        written.append(_entities().MarketBook.__tablename__)
    for table in (Entry, Transaction, Account):
        count = (
            await session.execute(select(func.count()).select_from(table))
        ).scalar_one()
        if count:
            written.append(table.__tablename__)
    return written


@contextmanager
def _capture_sql() -> Iterator[list[str]]:
    """Every statement the block sends to Postgres, in order, from the
    engine's `before_cursor_execute`."""
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
    """The criterion's own three cases. ADR 0017's `409 market_closed`."""
    upstream = _Upstream(status=status)

    with pytest.raises(_errors().MarketClosed):
        await _gate(session, upstream)


@pytest.mark.parametrize(
    "status", ["settled", "draft", "submitted", "halted"]
)
async def test_a_status_nobody_enumerated_is_refused_too(
    session: AsyncSession, status: str
) -> None:
    """"One comparison, not a list of statuses": a list-based gate agrees with
    `!= "open"` on the cases above and would accept these, `settled` first.
    """
    upstream = _Upstream(status=status)

    with pytest.raises(_errors().MarketClosed):
        await _gate(session, upstream)


async def test_an_open_market_is_allowed_through(session: AsyncSession) -> None:
    """The main flow. Returns `None`, not terms: the book's snapshot is the
    authority (ADR 0005)."""
    upstream = _Upstream(status="open")

    assert await _gate(session, upstream) is None


async def test_a_market_closed_early_with_a_future_close_time_is_refused(
    session: AsyncSession,
) -> None:
    """[2.3] #7: an early close leaves `close_time` in the future (ADR 0014),
    so a gate on a snapshotted `close_time` would accept these trades.
    Closed after the book was committed, as in production.
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


async def test_a_close_time_already_past_does_not_refuse_an_open_market(
    session: AsyncSession,
) -> None:
    """The complement: the gate reads the projection and never compares
    `close_time` itself (ADR 0017). Fails if a local comparison is added.
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
    """ADR 0017: a null `b` on the wire must not refuse trades on a book that
    snapshotted a good one. The gate reads `status` and nothing else.
    """
    upstream = _Upstream(status="open")
    await _warm(session, upstream)

    upstream.body["liquidity_b"] = None

    assert await _gate(session, upstream) is None


async def test_an_unpriceable_outcome_list_does_not_refuse_the_gate_either(
    session: AsyncSession,
) -> None:
    """The same for the outcome list: a book already stored passed that rule
    once, immutably."""
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
    """The session is for the 404 branch only. Reading the book up front would
    pass every other test and cost a read on every trade.
    """
    upstream = _Upstream(status="open")

    with _capture_sql() as statements:
        await _gate(session, upstream)

    assert statements == [], (
        "the gate's only job on an open market is one HTTP call; it sent "
        f"{len(statements)} statement(s) to Postgres:\n" + "\n".join(statements)
    )


async def test_a_refused_gate_writes_nothing(session: AsyncSession) -> None:
    """A refusal leaves the database untouched.

    Asked of the request's own session first, with no rollback in between (a
    rollback would erase the writes being looked for), then of a second
    session for the committed state.
    """
    upstream = _Upstream(status="closed")

    with pytest.raises(_errors().MarketClosed):
        await _gate(session, upstream)

    assert await _written(session, upstream.market_id) == [], (
        "a refused gate left writes in the request's own session"
    )

    async with get_session_factory()() as observer:
        assert await _written(observer, upstream.market_id) == [], (
            "a refused gate committed writes"
        )


async def test_the_404_branch_read_is_unlocked(session: AsyncSession) -> None:
    """ADR 0015 governs reads that decide a write; this one decides an error
    code, so it takes no lock on the hottest row there is."""
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
    """One hop per gate, at `GET /public/markets/{id}`, whose `status` is the
    derived one (D-022). ADR 0005's budget."""
    upstream = _Upstream(status="open")

    await _gate(session, upstream)

    assert upstream.calls == 1
    assert upstream.requests[0].method == "GET"
    assert upstream.requests[0].url.path == f"/public/markets/{upstream.market_id}"


async def test_a_market_s_first_trade_asks_twice_and_every_later_one_once(
    session: AsyncSession,
) -> None:
    """ADR 0017: two hops on the trade that creates the book, one after. If it
    reads one, somebody handed `MarketTerms` to the trade path.
    """
    upstream = _Upstream(status="open")
    token = _token()

    async def trade_prelude() -> None:
        await _gate(session, upstream, access_token=token)
        await _books().ensure_open(
            session, upstream.market_id, access_token=token,
            terms_client=terms_client_over(upstream.transport),
        )

    await trade_prelude()
    assert upstream.calls == 2, "the first trade on a market asks twice"

    upstream.calls = 0
    await trade_prelude()
    assert upstream.calls == 1, "every later trade asks once"


async def test_the_caller_s_own_token_is_forwarded_and_none_is_minted(
    session: AsyncSession,
) -> None:
    """D-018: the caller's own token, and none minted here, on every trade."""
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
    """503, not 409: ADR 0017 fails closed, but a sick dependency must not read
    as a permanently closed market."""
    upstream = _Upstream(status="open")
    upstream.raises = httpx.ConnectError("market service is down")

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


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
    """Every case is `!= "open"`, so a naive gate would silently report every
    market closed. ADR 0017: a parseable status, or a 503.
    """
    upstream = _Upstream(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


async def test_a_body_without_settleable_is_unavailable_at_the_gate(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "parsed strictly on every pull": the trade path reads no
    settlement field, and still refuses a body missing one. The status is
    "open", so the missing key is the only thing that can refuse it.
    """
    upstream = _Upstream()
    del upstream.body["settleable"]

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


async def test_a_404_on_a_market_with_a_book_is_unavailable_not_not_found(
    session: AsyncSession,
) -> None:
    """The row in ADR 0017's table that looks wrong and is not: a market with
    a book existed, so its 404 is market_service answering incorrectly.

    The `_book_row` read leaves a transaction open, so the gate releases it
    before the call and its own `books.find` runs after the release (#115):
    this is the test that the read still finds the book.
    """
    upstream = _Upstream(status="open")
    await _warm(session, upstream)
    assert await _book_row(session, upstream.market_id) is not None
    assert session.in_transaction()

    upstream.status_code = 404
    upstream.body = {"code": "market_not_found"}

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _gate(session, upstream)


async def test_a_404_on_a_market_with_no_book_is_not_found(
    session: AsyncSession,
) -> None:
    """The other branch: with no book, a 404 is the ordinary first trade on a
    market that does not exist. ADR 0017.
    """
    upstream = _Upstream(status="open", status_code=404)
    upstream.body = {"code": "market_not_found"}

    assert await _book_row(session, upstream.market_id) is None

    with pytest.raises(_errors().MarketNotFound):
        await _gate(session, upstream)


async def test_a_404_the_gate_saw_spares_the_cold_path_its_call(
    session: AsyncSession,
) -> None:
    """The memory sits in `market_terms.fetch`, so both callers share
    it. A trade on an unpublished id, retried, asks market_service once."""
    upstream = _Upstream(status="open", status_code=404)
    upstream.body = {"code": "market_not_found"}

    with pytest.raises(_errors().MarketNotFound):
        await _gate(session, upstream)
    with pytest.raises(_errors().MarketNotFound):
        await _gate(session, upstream)
    with pytest.raises(_errors().MarketNotFound):
        await _books().ensure_open(
            session,
            upstream.market_id,
            access_token=_token(),
            terms_client=terms_client_over(upstream.transport),
        )

    assert upstream.calls == 1


async def test_an_upstream_401_stays_not_authenticated(
    session: AsyncSession,
) -> None:
    """The forwarded token was rejected, a fact about the caller: not a
    dependency failure, and not a closed market."""
    upstream = _Upstream(status="open", status_code=401)
    upstream.body = {"code": "invalid_token"}

    with pytest.raises(_errors().NotAuthenticated):
        await _gate(session, upstream)


# =========================================================================
# The connection: none held while market_service answers (#115, D-043)
# =========================================================================
async def test_the_gate_holds_no_connection_while_market_service_is_slow(
    session: AsyncSession,
) -> None:
    """A read first, standing in for the trade path's replay lookup, autobegins
    a transaction nothing ends. The gate releases it before it calls out, so a
    slow market_service holds no pooled connection on any caller's behalf.

    Checked from the session, the pool and Postgres, at the stalled call.
    Remove the rollback in `market_status.ensure_trading` and this goes red.
    """
    upstream = _Upstream(status="open")
    pool = get_engine().pool
    stalled = Stalled(
        upstream,
        lambda: {
            "session in a transaction": session.in_transaction(),
            "pooled connections checked out": pool.checkedout(),
        },
    )
    replay_key = f"trade:{uuid.uuid4()}"
    await session.execute(
        select(Transaction.id).where(Transaction.idempotency_key == replay_key)
    )
    assert session.in_transaction(), "the read opened no transaction to release"

    task = asyncio.create_task(
        _status().ensure_trading(
            session,
            upstream.market_id,
            access_token=_token(),
            terms_client=terms_client_over(stalled.transport),
        )
    )
    try:
        async with asyncio.timeout(10):
            await stalled.entered.wait()
        idle = await idle_in_transaction()
    finally:
        stalled.release.set()
        await task

    clean = {"session in a transaction": False, "pooled connections checked out": 0}
    assert stalled.seen == [clean]
    assert idle == 0, f"{idle} backend(s) idle in transaction across the gate's call"


async def test_the_gate_refuses_to_run_over_a_pending_write(
    session: AsyncSession,
) -> None:
    """Its rollback would discard the write silently, so the precondition is
    enforced rather than documented, as `books.ensure_open`'s is."""
    upstream = _Upstream(status="open")
    pending = Account(kind=_entities().AccountKind.USER, owner_id=uuid.uuid4())
    session.add(pending)

    with pytest.raises(AssertionError, match="nothing pending"):
        await _gate(session, upstream)

    assert pending in session.new, "the pending write was discarded"
    assert upstream.calls == 0


# =========================================================================
# The copy of "open", pinned against market_service's source
# =========================================================================
def _enum_member(path: pathlib.Path, enum: str, member: str) -> str:
    """One enum member's string value, read out of a source file with `ast`,
    since market_service cannot be imported (`test_import_boundary.py`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != enum:
            continue
        for stmt in node.body:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else []
            for target in targets:
                if isinstance(target, ast.Name) and target.id == member:
                    value = stmt.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value
                    raise AssertionError(
                        f"{enum}.{member} in {path} is not a plain string literal"
                    )

    raise AssertionError(f"{enum}.{member} not found in {path}")


def test_the_open_status_matches_the_one_market_service_puts_on_the_wire() -> None:
    """The money gate's copy of `"open"`, pinned against its source: a wrong
    value refuses every trade silently."""
    theirs = _enum_member(
        _BACKEND / "market_service" / "model" / "entities.py",
        "MarketStatus",
        "OPEN",
    )

    assert _status()._OPEN == theirs, (
        f"this service gates trading on {_status()._OPEN!r} and market_service "
        f"publishes {theirs!r}; every trade would be refused as market_closed"
    )


@pytest.mark.parametrize(
    ("ours", "member"), [("_APPROVED", "APPROVED"), ("_SETTLED", "SETTLED")]
)
def test_the_settlement_statuses_match_the_ones_market_service_puts_on_the_wire(
    ours: str, member: str
) -> None:
    """Settlement's copies of `"approved"` and `"settled"`, pinned the same
    way. The fake upstream in `settlement_fixtures.py` types the same literal,
    so a rename upstream would refuse every settlement as market_not_approved
    with every settlement test still green."""
    from service import settlement  # noqa: PLC0415

    theirs = _enum_member(
        _BACKEND / "market_service" / "model" / "entities.py",
        "MarketStatus",
        member,
    )

    assert getattr(settlement, ours) == theirs, (
        f"settlement reads {getattr(settlement, ours)!r} and market_service "
        f"publishes {theirs!r}; every settlement would be refused"
    )
