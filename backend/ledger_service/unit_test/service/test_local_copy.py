"""The ledger's local copy of who may trade and what may be traded. #640, ADR 0030.

The copy fails closed: never loaded, or older than its max age, both checks
refuse with the dependency's own unavailable code. A market absent from a
fresh copy gets one refresh before it is refused, as not found or, if it
holds a book, as unavailable (ADR 0017's last row). The fetches parse the two
`/internal/` answers and refuse anything else, so the refresher keeps the old
copy. `_OPEN` and the close-time predicate are restated from market_service
and pinned against its source.

Only the miss path reads the database. The other `ensure_trading` tests pass
a session that fails the test if it is touched: a market the copy holds is
answered in memory.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.book_fixtures import Upstream, warm
from unit_test.conftest import ManualClock, auth_client_over, terms_client_over
from unit_test.source_pins import BACKEND, enum_member_of
from unit_test.trade_fixtures import reached


def _local_copy():
    """`service/local_copy.py`, imported inside each test so a missing name
    fails one test rather than collection (D-007)."""
    from service import local_copy  # noqa: PLC0415

    return local_copy


def _errors():
    """`core/errors.py`, reached lazily like `_local_copy`."""
    from core import errors  # noqa: PLC0415

    return errors


def _entities():
    """`model/entities.py`, reached lazily like `_local_copy`."""
    from model import entities  # noqa: PLC0415

    return entities


_MAX_AGE = 5.0
_MONOTONIC_NOW = 1_000.0
# A second before `_MONOTONIC_NOW`: fresh, and past the miss-refresh floor, so
# a miss refreshes unless a test says otherwise.
_LOADED_AT = _MONOTONIC_NOW - 1.0
_NOW = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
_FUTURE = _NOW + timedelta(days=30)
_SERVICE_KEY = "local-copy-test-key-0123456789abcdef"
_AS_OF = "2026-11-05T09:30:00.000000Z"


class _Untouchable:
    """A session `ensure_trading` must not use: a market the copy holds is
    answered in memory, with no statement sent to Postgres."""

    def __getattr__(self, name: str):
        raise AssertionError(f"ensure_trading touched the session ({name})")


_NO_DATABASE = cast(AsyncSession, _Untouchable())


def _clock_at(now: float = _MONOTONIC_NOW) -> ManualClock:
    clock = ManualClock()
    clock.now = now
    return clock


def _copy(
    *,
    suspended: frozenset[uuid.UUID] | None = frozenset(),
    markets: Mapping[uuid.UUID, object] | None = None,
    loaded_at: float = _LOADED_AT,
    clock: ManualClock | None = None,
):
    """A hand-built copy, loaded at `loaded_at`, on `clock` (`_MONOTONIC_NOW`
    unless given). `None` leaves that half never loaded."""
    module = _local_copy()
    copy = module.LocalCopy(
        max_age_seconds=_MAX_AGE, clock=clock if clock is not None else _clock_at()
    )
    as_of = datetime(2026, 11, 5, tzinfo=UTC)
    if suspended is not None:
        copy.suspended = module.SuspendedUsers(
            user_ids=suspended, loaded_at=loaded_at, as_of=as_of
        )
    if markets is not None:
        copy.markets = module.PublishedMarkets(
            terms=markets, loaded_at=loaded_at, as_of=as_of
        )
    return copy


def _terms(status: str = "open", close_time: datetime | None = _FUTURE):
    return _local_copy().MarketTradingTerms(status=status, close_time=close_time)


async def _ensure(
    copy,
    market_id: uuid.UUID,
    session: AsyncSession = _NO_DATABASE,
):
    """`ensure_trading` at `_NOW`, every age read from the copy's own clock."""
    return await _local_copy().ensure_trading(session, copy, market_id, now=_NOW)


def _refuse(copy, user_id: uuid.UUID) -> None:
    _local_copy().refuse_if_suspended(copy, user_id)


# =========================================================================
# Failing closed
# =========================================================================
async def test_a_copy_never_loaded_refuses_with_the_dependency_s_unavailable_code() -> None:
    """Before the first refresh lands, nothing is known, so nothing is
    allowed: a 503 naming the dependency, never a pass. Catches: a `None`
    half read as "nobody suspended" or as "no market", which would be a pass
    and a 404."""
    copy = _copy(suspended=None, markets=None)

    with pytest.raises(_errors().AuthUnavailable):
        _refuse(copy, uuid.uuid4())
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _ensure(copy, uuid.uuid4())


async def test_a_copy_older_than_the_max_age_refuses_with_the_dependency_s_unavailable_code() -> None:
    """ADR 0030's refusal bound: a copy past `max_age_seconds` is not trusted,
    whatever it holds. The market is open in it and the user is not
    suspended, so only staleness can refuse. A copy exactly at the max age is
    still trusted. Catches: no freshness check; `<` for `<=`; the age
    measured the wrong way round."""
    market_id = uuid.uuid4()
    stale = _copy(
        markets={market_id: _terms()}, loaded_at=_MONOTONIC_NOW - _MAX_AGE - 0.001
    )
    at_the_bound = _copy(
        markets={market_id: _terms()}, loaded_at=_MONOTONIC_NOW - _MAX_AGE
    )

    with pytest.raises(_errors().AuthUnavailable):
        _refuse(stale, uuid.uuid4())
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _ensure(stale, market_id)

    _refuse(at_the_bound, uuid.uuid4())
    assert await _ensure(at_the_bound, market_id) == _terms()


def test_a_suspended_id_is_refused_and_an_active_one_passes() -> None:
    """ADR 0022's check, answered from the copy: a listed id is
    `AccountSuspended`, an id not listed trades."""
    suspended_id = uuid.uuid4()
    copy = _copy(suspended=frozenset({suspended_id}))

    with pytest.raises(_errors().AccountSuspended):
        _refuse(copy, suspended_id)
    _refuse(copy, uuid.uuid4())


# =========================================================================
# A market the copy does not hold
# =========================================================================
async def test_a_market_absent_from_the_copy_with_no_book_is_not_found(
    session: AsyncSession,
) -> None:
    """ADR 0017: with no book, absence is a market that does not exist."""
    with pytest.raises(_errors().MarketNotFound):
        await _ensure(_copy(markets={}), uuid.uuid4(), session)


async def test_a_market_absent_from_the_copy_but_holding_a_book_is_unavailable(
    session: AsyncSession,
) -> None:
    """ADR 0017's last row: a market with a book was published, so its
    absence is market_service answering wrongly, a 503 and not a 404."""
    upstream = Upstream()
    await warm(session, upstream)

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _ensure(_copy(markets={}), upstream.market_id, session)


class _RefreshStub:
    """`refresh_markets` as the lifespan installs it, counting its awaits.
    `adds` is put into the copy's markets, as a refresh that found it would.

    Stamped as a fetch stamps it, at `clock`'s time before the request; the
    request then takes `takes` seconds of that clock. `session`, if given,
    is asked whether it is in a transaction while the refresh runs."""

    def __init__(
        self,
        copy,
        adds: Mapping[uuid.UUID, object],
        *,
        clock: ManualClock,
        takes: float = 0.0,
        session: AsyncSession | None = None,
    ) -> None:
        self.copy = copy
        self.adds = adds
        self.clock = clock
        self.takes = takes
        self.session = session
        self.calls = 0
        self.in_transaction: list[bool] = []

    async def __call__(self) -> None:
        self.calls += 1
        if self.session is not None:
            self.in_transaction.append(self.session.in_transaction())
        loaded_at = self.clock.now
        self.clock.now += self.takes
        self.copy.markets = _local_copy().PublishedMarkets(
            terms={**self.copy.markets.terms, **self.adds},
            loaded_at=loaded_at,
            as_of=datetime(2026, 11, 5, tzinfo=UTC),
        )


@pytest.mark.parametrize("published_since", [True, False], ids=["found", "still-absent"])
async def test_a_market_missing_from_the_copy_triggers_one_refresh_before_it_is_refused(
    session: AsyncSession, published_since: bool
) -> None:
    """A market published since the last refresh trades on its first
    request, not a second later; one that is still absent is refused after
    exactly one refresh, not a loop of them. Catches: no refresh on a miss
    (the found case is 404); the re-read skipped; a retry loop."""
    market_id = uuid.uuid4()
    clock = _clock_at()
    copy = _copy(markets={}, clock=clock)
    stub = _RefreshStub(
        copy, {market_id: _terms()} if published_since else {}, clock=clock
    )
    copy.refresh_markets = stub

    if published_since:
        assert await _ensure(copy, market_id, session) == _terms()
    else:
        with pytest.raises(_errors().MarketNotFound):
            await _ensure(copy, market_id, session)

    assert stub.calls == 1


async def test_a_refresh_that_fails_on_a_miss_is_unavailable_not_not_found(
    session: AsyncSession,
) -> None:
    """A miss whose refresh could not reach market_service knows nothing new
    about the market, so the answer names the dependency. Catches: the
    refresh's error escaping as a 500, or swallowed into a 404."""

    async def unreachable() -> None:
        raise httpx.ConnectError("market service is down")

    copy = _copy(markets={})
    copy.refresh_markets = unreachable

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _ensure(copy, uuid.uuid4(), session)


async def test_a_refresh_that_lands_older_than_the_max_age_refuses_the_trade(
    session: AsyncSession,
) -> None:
    """The age is checked again after the refresh, on the copy's clock. A
    fetch may answer more than the max age after it was stamped (each phase
    has its own five-second timeout), and a queued caller may be answered by
    a run that started long before its miss; either way the terms are too old
    to trade on. Catches: freshness checked only on entry."""
    market_id = uuid.uuid4()
    clock = _clock_at()
    copy = _copy(markets={}, clock=clock)
    copy.refresh_markets = _RefreshStub(
        copy, {market_id: _terms()}, clock=clock, takes=_MAX_AGE + 0.001
    )

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _ensure(copy, market_id, session)


async def test_a_miss_holds_no_transaction_while_it_refreshes(
    session: AsyncSession,
) -> None:
    """The trade path reads before the gate, so the session arrives in a
    transaction. Held across the refresh, it is a pooled connection idle in
    transaction for up to the fetch's timeout, per miss. D-043. Catches: no
    release before the refresh."""
    await session.get(_entities().Account, uuid.uuid4())
    assert session.in_transaction()
    clock = _clock_at()
    copy = _copy(markets={}, clock=clock)
    stub = _RefreshStub(copy, {}, clock=clock, session=session)
    copy.refresh_markets = stub

    with pytest.raises(_errors().MarketNotFound):
        await _ensure(copy, uuid.uuid4(), session)

    assert stub.in_transaction == [False]


async def test_a_miss_refuses_to_refresh_over_a_pending_write(
    session: AsyncSession,
) -> None:
    """The release is a rollback, which would discard the write silently, so
    the precondition is enforced rather than documented, as `books.ensure_open`'s
    is. Nothing is fetched."""
    pending = _entities().Account(
        kind=_entities().AccountKind.USER, owner_id=uuid.uuid4()
    )
    session.add(pending)
    clock = _clock_at()
    copy = _copy(markets={}, clock=clock)
    stub = _RefreshStub(copy, {}, clock=clock)
    copy.refresh_markets = stub

    with pytest.raises(AssertionError, match="nothing pending"):
        await _ensure(copy, uuid.uuid4(), session)

    assert pending in session.new, "the pending write was discarded"
    assert stub.calls == 0


async def test_misses_within_the_floor_share_one_refresh_and_a_later_one_refreshes(
    session: AsyncSession,
) -> None:
    """A stream of trades on ids that do not exist costs market_service at
    most one full-list fetch per floor, per replica. A miss inside the floor
    goes straight to the absent-market answer. Catches: no floor (two
    fetches by the second miss); a floor that never lifts (one fetch by the
    third)."""
    clock = _clock_at()
    copy = _copy(markets={}, clock=clock)
    stub = _RefreshStub(copy, {}, clock=clock)
    copy.refresh_markets = stub
    floor = _local_copy()._MISS_REFRESH_FLOOR_SECONDS

    for seconds_later, fetches in [(0.0, 1), (floor / 2, 1), (floor + 0.05, 2)]:
        clock.now = _MONOTONIC_NOW + seconds_later
        with pytest.raises(_errors().MarketNotFound):
            await _ensure(copy, uuid.uuid4(), session)
        assert stub.calls == fetches, f"{seconds_later}s after the first miss"


# =========================================================================
# The predicate, answered from the copy
# =========================================================================
@pytest.mark.parametrize(
    "status", ["closed", "pending_resolution", "approved", "settled", "voided"]
)
async def test_a_market_whose_stored_status_is_not_open_is_closed(status: str) -> None:
    """A future close time does not reopen a market an administrator closed
    or one that has moved on to resolution. ADR 0014, ADR 0017."""
    market_id = uuid.uuid4()

    with pytest.raises(_errors().MarketClosed):
        await _ensure(_copy(markets={market_id: _terms(status=status)}), market_id)


@pytest.mark.parametrize(
    "close_time", [_NOW - timedelta(microseconds=1), _NOW], ids=["passed", "now"]
)
async def test_a_close_time_that_has_passed_refuses_a_trade_whatever_the_stored_status_says(
    close_time: datetime,
) -> None:
    """ADR 0011: the clock closes a market, and the stored `open` is only the
    sweep not having written it down yet. The edge is exclusive: at
    `close_time` itself trading has stopped. Catches: gating on the status
    alone; `<` for `<=`."""
    market_id = uuid.uuid4()
    copy = _copy(markets={market_id: _terms(close_time=close_time)})

    with pytest.raises(_errors().MarketClosed):
        await _ensure(copy, market_id)


async def test_a_market_with_no_close_time_is_closed() -> None:
    """market_service's predicate reads a null close time as closed, the
    safer failure, so the restatement must too. Catches: a null treated as
    "never closes"."""
    market_id = uuid.uuid4()
    copy = _copy(markets={market_id: _terms(close_time=None)})

    with pytest.raises(_errors().MarketClosed):
        await _ensure(copy, market_id)


async def test_an_open_market_with_a_future_close_time_passes_and_returns_its_terms() -> None:
    """The one shape that trades, and its terms come back for the lock's
    re-check of `close_time` (Task 4.3)."""
    market_id = uuid.uuid4()
    terms = _terms()

    assert await _ensure(_copy(markets={market_id: terms}), market_id) is terms


# =========================================================================
# The fetches
# =========================================================================
class _Recording:
    """An upstream that answers `status_code` and `content` to every request,
    and keeps the requests."""

    def __init__(
        self, status_code: int = 200, *, json: object = None, content: bytes | None = None
    ) -> None:
        self.status_code = status_code
        self.json = json
        self.content = content
        self.requests: list[httpx.Request] = []
        self.requested_at: list[float] = []

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.requested_at.append(time.monotonic())
            if self.content is not None:
                return httpx.Response(self.status_code, content=self.content)
            return httpx.Response(self.status_code, json=self.json)

        return httpx.MockTransport(handler)


async def test_fetch_suspended_sends_the_key_and_parses_the_body() -> None:
    """`GET /internal/users/suspended` with `X-Service-Key`, every id parsed.
    `loaded_at` is the monotonic clock, the one the freshness checks compare
    against, read before the request: the copy's age then never understates
    how old auth's answer is. Catches: `loaded_at` read after the answer."""
    ids = [uuid.uuid4(), uuid.uuid4()]
    upstream = _Recording(json={"as_of": _AS_OF, "user_ids": [str(i) for i in ids]})
    before = time.monotonic()

    async with auth_client_over(upstream.transport) as client:
        suspended = await _local_copy().fetch_suspended(client, service_key=_SERVICE_KEY)

    [request] = upstream.requests
    assert (request.method, request.url.path) == ("GET", "/internal/users/suspended")
    assert request.headers["X-Service-Key"] == _SERVICE_KEY
    assert suspended.user_ids == frozenset(ids)
    assert suspended.as_of == datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
    assert before <= suspended.loaded_at <= upstream.requested_at[0]


async def test_fetch_published_markets_sends_the_key_and_parses_statuses_and_close_times() -> None:
    """`GET /internal/markets/published` with `X-Service-Key`, each market's
    stored status and close time kept as sent. A close time with no offset is
    read as UTC, as market_service's predicate reads one. `loaded_at` as the
    suspended fetch's. Catches: `time.time()` for `time.monotonic()`, which
    would leave a copy that never goes stale."""
    open_id, settled_id, no_offset_id, unclosed_id = (uuid.uuid4() for _ in range(4))
    rows = [
        (open_id, "open", "2026-11-30T16:00:00Z"),
        (settled_id, "settled", "2026-10-31T16:00:00+08:00"),
        (no_offset_id, "open", "2026-12-01T00:00:00"),
        (unclosed_id, "open", None),
    ]
    upstream = _Recording(
        json={
            "as_of": _AS_OF,
            "markets": [
                {"id": str(market_id), "status": status, "close_time": close_time}
                for market_id, status, close_time in rows
            ],
        }
    )
    before = time.monotonic()

    async with terms_client_over(upstream.transport) as client:
        markets = await _local_copy().fetch_published_markets(
            client, service_key=_SERVICE_KEY
        )

    [request] = upstream.requests
    assert (request.method, request.url.path) == ("GET", "/internal/markets/published")
    assert request.headers["X-Service-Key"] == _SERVICE_KEY
    assert dict(markets.terms) == {
        open_id: _terms("open", datetime(2026, 11, 30, 16, tzinfo=UTC)),
        settled_id: _terms("settled", datetime(2026, 10, 31, 8, tzinfo=UTC)),
        no_offset_id: _terms("open", datetime(2026, 12, 1, tzinfo=UTC)),
        unclosed_id: _terms("open", None),
    }
    assert markets.as_of == datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
    assert before <= markets.loaded_at <= upstream.requested_at[0]


def _refresh_suspended(copy, transport: httpx.MockTransport):
    async def refresh() -> None:
        async with auth_client_over(transport) as client:
            await _local_copy().refresh_suspended(copy, client, service_key=_SERVICE_KEY)

    return refresh


def _refresh_markets(copy, transport: httpx.MockTransport):
    async def refresh() -> None:
        async with terms_client_over(transport) as client:
            await _local_copy().refresh_published_markets(
                copy, client, service_key=_SERVICE_KEY
            )

    return refresh


# Keyed by the half of the copy each one replaces.
_REFRESHES: dict[str, Callable] = {
    "suspended": _refresh_suspended,
    "markets": _refresh_markets,
}


@pytest.mark.parametrize("which", list(_REFRESHES))
async def test_a_401_on_refresh_is_logged_at_warning_and_the_old_copy_stays(
    which: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A 401 to the service key means the two services hold different keys,
    and every refresh will fail the same way until a person fixes it, so it
    is a WARNING of its own. The copy already held stays, to age out and fail
    closed. Matched by level and logger, never by the message's text.
    Catches: a 401 read as an empty list; the copy replaced or cleared."""
    copy = _copy(markets={})
    old = getattr(copy, which)
    upstream = _Recording(401, json={"error": {"code": "invalid_service_key"}})
    caplog.set_level(logging.INFO)

    with pytest.raises(ValueError):
        await _REFRESHES[which](copy, upstream.transport)()

    assert getattr(copy, which) is old
    assert [
        record.levelno
        for record in caplog.records
        if record.name == "service.local_copy"
    ] == [logging.WARNING]


_SUSPENDED_ID = str(uuid.uuid4())
_MARKET = {"id": str(uuid.uuid4()), "status": "open", "close_time": "2026-11-30T16:00:00Z"}


def _markets_body(*markets: object) -> _Recording:
    return _Recording(json={"as_of": _AS_OF, "markets": list(markets)})


_MALFORMED: list[tuple[str, _Recording]] = [
    ("suspended", _Recording(503, json={"as_of": _AS_OF, "user_ids": []})),
    ("suspended", _Recording(content=b"not json")),
    ("suspended", _Recording(json=[_SUSPENDED_ID])),
    ("suspended", _Recording(json={"as_of": _AS_OF, "user_ids": {}})),
    ("suspended", _Recording(json={"as_of": _AS_OF, "user_ids": ["not-a-uuid"]})),
    ("suspended", _Recording(json={"as_of": _AS_OF, "user_ids": [7]})),
    ("suspended", _Recording(json={"user_ids": [_SUSPENDED_ID]})),
    ("markets", _Recording(json={"as_of": _AS_OF, "markets": {}})),
    ("markets", _markets_body("open")),
    ("markets", _markets_body({**_MARKET, "status": 1})),
    ("markets", _markets_body({**_MARKET, "close_time": "soon"})),
    ("markets", _markets_body({**_MARKET, "close_time": 1})),
    ("markets", _markets_body({"status": "open", "close_time": None})),
]


@pytest.mark.parametrize(
    ("which", "upstream"),
    _MALFORMED,
    ids=[
        "503",
        "not-json",
        "not-an-object",
        "ids-not-a-list",
        "id-not-a-uuid",
        "id-not-a-string",
        "no-as-of",
        "markets-not-a-list",
        "market-not-an-object",
        "status-not-a-string",
        "close-time-unparseable",
        "close-time-not-a-string",
        "market-without-an-id",
    ],
)
async def test_a_malformed_body_leaves_the_old_copy_in_place(
    which: str, upstream: _Recording
) -> None:
    """Anything but a well-formed 200 is refused as a `ValueError`, which the
    refresher logs, and the copy already held stays. A list that only half
    parses is never installed: a missing suspension is a suspended trader
    trading. Catches: a non-200 parsed as if it were a 200; a bad row
    skipped instead of the whole answer refused; an empty object iterated
    as an empty list, which would install "nobody is suspended"."""
    copy = _copy(markets={})
    old = getattr(copy, which)

    with pytest.raises(ValueError):
        await _REFRESHES[which](copy, upstream.transport)()

    assert getattr(copy, which) is old


async def test_a_well_formed_refresh_replaces_the_copy_whole() -> None:
    """The success side of the two above: a fresh snapshot is installed, not
    merged into the old one, so an id unsuspended since leaves the copy, and
    so does a market no longer listed. Loaded a second before the refresh so
    the fetched snapshot is the newer. Catches: `{**old, **new}` on either
    half."""
    unsuspended, still_suspended = uuid.uuid4(), uuid.uuid4()
    gone, still_listed = uuid.uuid4(), uuid.uuid4()
    copy = _copy(
        suspended=frozenset({unsuspended, still_suspended}),
        markets={gone: _terms(), still_listed: _terms()},
        loaded_at=time.monotonic() - 1.0,
    )
    suspended_upstream = _Recording(
        json={"as_of": _AS_OF, "user_ids": [str(still_suspended)]}
    )
    markets_upstream = _markets_body(
        {"id": str(still_listed), "status": "open", "close_time": "2026-11-30T16:00:00Z"}
    )

    await _refresh_suspended(copy, suspended_upstream.transport)()
    await _refresh_markets(copy, markets_upstream.transport)()

    assert copy.suspended.user_ids == frozenset({still_suspended})
    assert set(copy.markets.terms) == {still_listed}


_EMPTY_BODIES = {
    "suspended": {"as_of": _AS_OF, "user_ids": []},
    "markets": {"as_of": _AS_OF, "markets": []},
}


@pytest.mark.parametrize("which", list(_REFRESHES))
async def test_an_older_snapshot_arriving_after_a_newer_one_is_dropped(
    which: str,
) -> None:
    """Two refreshes may overlap: the lifespan's loop and a miss's. If the
    one stamped first answers last, installing it would put the copy back in
    time, and back past the max age sooner than it should. Here a newer
    snapshot is installed while this fetch is in flight. Catches: installing
    whatever arrives."""
    copy = _copy(markets={}, loaded_at=time.monotonic() - 1.0)
    installed_meanwhile = []

    def handler(request: httpx.Request) -> httpx.Response:
        newer = replace(getattr(copy, which), loaded_at=time.monotonic())
        setattr(copy, which, newer)
        installed_meanwhile.append(newer)
        return httpx.Response(200, json=_EMPTY_BODIES[which])

    await _REFRESHES[which](copy, httpx.MockTransport(handler))()

    assert getattr(copy, which) is installed_meanwhile[0]


# =========================================================================
# Coalescing and the refresher loop
# =========================================================================
async def test_concurrent_refreshes_share_one_fetch_and_a_later_one_fetches_again() -> None:
    """A burst of trades on a just-published market each miss the copy, and
    must cost market_service one fetch, not one each. A miss after that fetch
    finished asks again. Catches: no coalescing (two fetches); a plain lock,
    which queues the second caller behind the first and then fetches again;
    a refresh that runs once ever."""
    fetches = 0
    release = asyncio.Event()
    started = asyncio.Event()

    async def fetch() -> None:
        nonlocal fetches
        fetches += 1
        started.set()
        await release.wait()

    shared = _local_copy().coalesced(fetch)
    first = asyncio.create_task(shared())
    await reached(started, "the first refresh never started", [first])
    second = asyncio.create_task(shared())
    for _ in range(3):
        await asyncio.sleep(0)
    assert not second.done(), "the second caller did not wait for the shared run"
    release.set()
    await asyncio.gather(first, second)

    assert fetches == 1

    await shared()

    assert fetches == 2


async def test_callers_queued_behind_a_failed_refresh_share_its_error() -> None:
    """Single flight on failure too: during an outage a queued caller gets
    the shared run's error at once, rather than each retrying in turn, so the
    k-th waits one timeout, not k, and market_service sees one attempt, not
    one per caller. Catches: a failed run counted as nothing shared."""
    fetches = 0
    release = asyncio.Event()
    started = asyncio.Event()

    async def fetch() -> None:
        nonlocal fetches
        fetches += 1
        started.set()
        await release.wait()
        raise httpx.ConnectError("market service is down")

    shared = _local_copy().coalesced(fetch)
    first = asyncio.create_task(shared())
    await reached(started, "the first refresh never started", [first])
    second = asyncio.create_task(shared())
    for _ in range(3):
        await asyncio.sleep(0)
    release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)

    assert [type(outcome) for outcome in outcomes] == [httpx.ConnectError] * 2
    assert fetches == 1


class _FailsThenWaits:
    """Raises `failure` on the first call, sets `second_call` on the second and
    then waits until cancelled. `first_call` is set as the first call begins."""

    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.calls = 0
        self.first_call = asyncio.Event()
        self.second_call = asyncio.Event()

    async def __call__(self) -> None:
        self.calls += 1
        if self.calls == 1:
            self.first_call.set()
            raise self.failure
        self.second_call.set()
        await asyncio.Event().wait()


@contextlib.asynccontextmanager
async def _refresher_running(stand_in: _FailsThenWaits, *, interval_seconds: float):
    """The loop on a task, over the stand-in, cancelled and awaited on exit."""
    task = asyncio.create_task(
        _local_copy().run_refresher(
            "suspended", stand_in, interval_seconds=interval_seconds
        )
    )
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_a_refresh_that_raises_is_logged_at_error_and_does_not_stop_the_next(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refresher task that died is a copy that never refreshes again, and
    every trade fails closed five seconds later. A `RuntimeError`, not a
    fetch error, because the bug is a handler narrowed to the errors
    somebody expected. Catches: `except (httpx.HTTPError, ValueError)` (the
    task dies and `reached` raises its error); `except Exception: pass`."""
    failure = RuntimeError("not a fetch error")
    stand_in = _FailsThenWaits(failure)
    caplog.set_level(logging.INFO)

    async with _refresher_running(stand_in, interval_seconds=0.001) as task:
        await reached(stand_in.second_call, "the loop never refreshed again", [task])

    logged = [
        record
        for record in caplog.records
        if record.exc_info is not None and record.exc_info[1] is failure
    ]
    assert [record.levelno for record in logged] == [logging.ERROR]


class _FailsThenRecovers:
    """Raises `failures` times, succeeds once, then sets `waiting` and waits
    until cancelled."""

    def __init__(self, *, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self.waiting = asyncio.Event()

    async def __call__(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(f"failure {self.calls}")
        if self.calls == self.failures + 1:
            return
        self.waiting.set()
        await asyncio.Event().wait()


async def test_a_failure_streak_logs_one_error_then_warnings_then_its_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An upstream down for a minute is sixty failures a minute per half per
    replica, so only the first of a streak carries a traceback at ERROR; the
    repeats are WARNINGs without one, and a success after a streak says it
    recovered. Catches: every failure at ERROR (three ERRORs), the streak
    never reset (no INFO), or a traceback on every repeat."""
    stand_in = _FailsThenRecovers(failures=3)
    caplog.set_level(logging.INFO, logger="service.local_copy")

    task = asyncio.create_task(
        _local_copy().run_refresher("suspended", stand_in, interval_seconds=0.001)
    )
    try:
        await reached(stand_in.waiting, "the loop never refreshed after recovering", [task])
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    records = [
        record
        for record in caplog.records
        if record.name == "service.local_copy" and "started" not in record.getMessage()
    ]
    assert [record.levelno for record in records] == [
        logging.ERROR,
        logging.WARNING,
        logging.WARNING,
        logging.INFO,
    ]
    assert [record.exc_info is not None for record in records] == [True, False, False, False]
    assert "3" in records[-1].getMessage()


async def test_the_first_refresh_runs_before_the_first_sleep() -> None:
    """The copy is empty until its first refresh, and an empty copy refuses
    every trade, so the first one runs at once. Catches: sleep, then
    refresh."""
    stand_in = _FailsThenWaits(RuntimeError("first call"))

    async with _refresher_running(stand_in, interval_seconds=3600) as task:
        await reached(stand_in.first_call, "the first refresh waited an interval", [task])


# =========================================================================
# What is restated from market_service, pinned against its source
# =========================================================================
def test_the_open_status_matches_the_one_market_service_puts_on_the_wire() -> None:
    """The money gate's copy of `"open"`, pinned against its source: a wrong
    value refuses every trade silently."""
    theirs = enum_member_of(
        BACKEND / "market_service" / "model" / "entities.py",
        "MarketStatus",
        "OPEN",
    )

    assert _local_copy()._OPEN == theirs, (
        f"this service gates trading on {_local_copy()._OPEN!r} and market_service "
        f"publishes {theirs!r}; every trade would be refused as market_closed"
    )


def test_the_trading_predicate_restates_market_service_s_closing_rule() -> None:
    """`ensure_trading` restates ADR 0011's predicate rather than importing
    it: the clock closes at `close_time`, exclusive, and a null close time is
    closed. If market_service changes either half, this copy must change with
    it, so this fails first. Read with `ast` and compared unparsed, so a
    reformat or a comment does not break it."""
    path = BACKEND / "market_service" / "core" / "closing.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    [predicate] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "trading_is_open"
    ]
    statements = [ast.unparse(node) for node in ast.walk(predicate)]

    assert "if close_time is None:\n    return False" in statements, (
        f"{path} no longer refuses a null close_time; the ledger's copy still does"
    )
    comparisons = [
        ast.unparse(node) for node in ast.walk(predicate) if isinstance(node, ast.Compare)
    ]
    assert any(
        comparison.startswith("as_utc(close_time) > ") for comparison in comparisons
    ), f"{path} no longer trades strictly before close_time; the ledger's copy still does"
