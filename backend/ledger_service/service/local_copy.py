"""The ledger's local copy of who may trade and what may be traded. #640, ADR 0030.

Two background tasks refetch auth's suspended ids and market_service's
published markets, and a trade consults the copy in memory instead of calling
either service. Two bounds: a change upstream reaches the trade path within
one `local_copy_refresh_seconds`, and a copy older than
`local_copy_max_age_seconds` is not trusted. Past that the check fails
closed, with the dependency's own unavailable code: a stale copy is a
dependency that is not answering (ADR 0017, ADR 0022).

ADR 0011's predicate is restated once, in `_is_trading`, from market_service's
`core/closing.py::trading_is_open`; `test_local_copy.py` pins it and `_OPEN`
against that source. `run_refresher` catches bare `Exception` on purpose: a
refresher task that died is a copy that never refreshes, and every trade
fails closed five seconds later with nothing said.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core import observability
from core.clock import as_utc
from core.database import release_before_calling_out
from core.errors import (
    AccountSuspended,
    AuthUnavailable,
    MarketClosed,
    MarketNotFound,
    MarketTermsUnavailable,
)
from service import books

logger = logging.getLogger(__name__)

# The one status a trade may proceed against, restated rather than imported
# (see `books.MIN_OUTCOMES`). A wrong value would refuse every trade silently,
# so `test_local_copy.py` pins it against market_service's source.
_OPEN = "open"

# A miss refetches the published list only if the copy is older than this. A
# per-id negative cache absorbs only repeats of one id; this caps refetches per
# replica for random ids too, at the cost of at most one fail-closed 404 for a
# market published in the last quarter second.
_MISS_REFRESH_FLOOR_SECONDS = 0.25

_SUSPENDED_PATH = "/internal/users/suspended"
_PUBLISHED_PATH = "/internal/markets/published"


class UnreadableAnswer(ValueError):
    """auth or market_service answered something other than a well-formed
    `200`. A `ValueError`, so the refresher's handler and the miss path's
    both catch it with the parse errors it sits beside."""


@dataclass(frozen=True)
class MarketTradingTerms:
    """One published market as the copy holds it: the stored status and close
    time, nothing derived. `close_time` is always aware."""

    status: str
    close_time: datetime | None


@dataclass(frozen=True)
class SuspendedUsers:
    """Every suspended id, as of one fetch. `loaded_at` is `time.monotonic()`;
    `as_of` is auth's own clock, kept for logging."""

    user_ids: frozenset[uuid.UUID]
    loaded_at: float
    as_of: datetime


@dataclass(frozen=True)
class PublishedMarkets:
    """Every published market's terms, as of one fetch. Clocks as
    `SuspendedUsers`'."""

    terms: Mapping[uuid.UUID, MarketTradingTerms]
    loaded_at: float
    as_of: datetime


class LocalCopy:
    """Held on app.state; replaced whole by each refresh. Never mutated in place.

    `refresh_markets` is set by the lifespan, through `coalesced`, so a burst
    of misses shares one fetch. `None` until then, and a miss then refuses
    without refreshing. `clock` must be the one the fetches stamp `loaded_at`
    with, `time.monotonic`; it is replaceable for the suite, as
    `NotFoundCache`'s is.
    """

    def __init__(
        self,
        *,
        max_age_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        self.clock = clock
        self.suspended: SuspendedUsers | None = None
        self.markets: PublishedMarkets | None = None
        self.refresh_markets: Callable[[], Awaitable[None]] | None = None

    def is_suspended_fresh(self, *, now: float) -> bool:
        """Whether the suspended ids were loaded, at most the max age before
        `now` (`time.monotonic()`)."""
        if self.suspended is None:
            return False
        return now - self.suspended.loaded_at <= self.max_age_seconds

    def is_markets_fresh(self, *, now: float) -> bool:
        """Whether the published markets were loaded, at most the max age
        before `now` (`time.monotonic()`)."""
        if self.markets is None:
            return False
        return now - self.markets.loaded_at <= self.max_age_seconds


def _body_of(response: httpx.Response, *, upstream: str) -> dict:
    """A `200`'s decoded object. Raises `UnreadableAnswer` for any other
    status or a body that is not a JSON object, and logs a 401 at WARNING."""
    if response.status_code == 401:
        # The two services hold different LEDGER_SERVICE_KEYs, so every
        # refresh fails alike until a person fixes it, and trades fail closed.
        logger.warning("%s answered 401 to the service key", upstream)
    if response.status_code != 200:
        raise UnreadableAnswer(f"{upstream} answered {response.status_code}")
    try:
        body = response.json()
    except (ValueError, RecursionError) as exc:
        raise UnreadableAnswer(f"{upstream} answered a body that is not JSON") from exc
    if not isinstance(body, dict):
        raise UnreadableAnswer(f"{upstream} answered a body that is not an object")
    return body


def _list_in(body: dict, key: str) -> list:
    """`body[key]`, which must be a JSON array. A string would iterate as
    characters, and a missing key is a broken deploy, not an empty list."""
    value = body.get(key)
    if not isinstance(value, list):
        raise UnreadableAnswer(f"{key} is not a list")
    return value


def _string_of(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise UnreadableAnswer(f"{field} is not a string")
    return value


def _uuid_of(value: object) -> uuid.UUID:
    """An id, which the contract sends as a string. Raises `ValueError` for
    one that does not parse."""
    return uuid.UUID(_string_of(value, field="id"))


def _timestamp_of(value: object, *, field: str) -> datetime:
    """An ISO timestamp, read as UTC if it has no offset, as market_service's
    predicate reads one."""
    return as_utc(datetime.fromisoformat(_string_of(value, field=field)))


def _market_of(row: object) -> tuple[uuid.UUID, MarketTradingTerms]:
    """One row of the published list. Raises `UnreadableAnswer` or
    `ValueError` for a row that is not the documented three fields."""
    if not isinstance(row, dict):
        raise UnreadableAnswer("a market is not an object")
    close_time = row.get("close_time")
    if close_time is not None:
        close_time = _timestamp_of(close_time, field="close_time")
    terms = MarketTradingTerms(
        status=_string_of(row.get("status"), field="status"), close_time=close_time
    )
    return _uuid_of(row.get("id")), terms


def _terms_in(copy: LocalCopy, market_id: uuid.UUID) -> MarketTradingTerms | None:
    if copy.markets is None:
        return None
    return copy.markets.terms.get(market_id)


def _is_refresh_due(copy: LocalCopy, *, now: float) -> bool:
    """Whether a miss may refetch: a refresh is installed and the markets were
    loaded at least `_MISS_REFRESH_FLOOR_SECONDS` before `now`."""
    if copy.refresh_markets is None or copy.markets is None:
        return False
    return now - copy.markets.loaded_at >= _MISS_REFRESH_FLOOR_SECONDS


async def _terms_after_refresh(
    session: AsyncSession, copy: LocalCopy, market_id: uuid.UUID
) -> MarketTradingTerms | None:
    """The market's terms after one refresh, or None if it is still absent.

    Rolls the session back first, so the fetch holds no connection (D-043).
    Raises `MarketTermsUnavailable` if the refresh fails, or lands older than
    the max age on the copy's clock: either way the copy knows nothing
    current about the market.
    """
    await release_before_calling_out(session, caller="ensure_trading()")
    try:
        await copy.refresh_markets()
    except (httpx.HTTPError, ValueError) as exc:
        raise MarketTermsUnavailable from exc
    # Again after the await: a fetch can answer more than the max age after it
    # was stamped, and a queued caller can share a run older than its miss.
    if not copy.is_markets_fresh(now=copy.clock()):
        raise MarketTermsUnavailable
    return _terms_in(copy, market_id)


def _is_trading(terms: MarketTradingTerms, *, now: datetime) -> bool:
    """ADR 0011's predicate over the stored terms. Twin of market_service's
    `core/closing.py::trading_is_open`; `test_local_copy.py` pins the two."""
    if terms.status != _OPEN:
        return False
    if terms.close_time is None:
        return False
    return terms.close_time > now


def coalesced(refresh: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
    """`refresh`, wrapped so that callers who arrive while one runs share it
    rather than each starting another. A caller after it finished runs anew.

    Queued callers share a failure too: each raises the shared run's error,
    so during an outage the upstream sees one attempt, not one per caller. A
    run that was cancelled is not shared; the next caller runs its own.
    """
    lock = asyncio.Lock()
    finished_runs = 0
    last_failure: Exception | None = None

    async def refresh_once_for_every_waiter() -> None:
        nonlocal finished_runs, last_failure
        runs_when_called = finished_runs
        async with lock:
            if finished_runs != runs_when_called:
                if last_failure is not None:
                    raise last_failure
                return
            # Recorded and re-raised, never swallowed: the queued callers
            # raise it too.
            try:
                await refresh()
            except Exception as failure:
                last_failure = failure
                finished_runs += 1
                raise
            last_failure = None
            finished_runs += 1

    return refresh_once_for_every_waiter


async def fetch_suspended(
    auth_client: httpx.AsyncClient, *, service_key: str
) -> SuspendedUsers:
    """Every suspended id, from auth's `GET /internal/users/suspended`.

    Raises `httpx.HTTPError` if auth does not answer and `ValueError` for any
    answer but a well-formed `200`.
    """
    # Before the request, not after: auth reads after this, so the copy's age
    # is never understated by however long the answer took.
    loaded_at = time.monotonic()
    response = await auth_client.get(
        _SUSPENDED_PATH, headers={"X-Service-Key": service_key}
    )
    body = _body_of(response, upstream="auth")
    return SuspendedUsers(
        user_ids=frozenset(_uuid_of(raw) for raw in _list_in(body, "user_ids")),
        loaded_at=loaded_at,
        as_of=_timestamp_of(body.get("as_of"), field="as_of"),
    )


async def fetch_published_markets(
    terms_client: httpx.AsyncClient, *, service_key: str
) -> PublishedMarkets:
    """Every published market's stored status and close time, from
    market_service's `GET /internal/markets/published`.

    Raises `httpx.HTTPError` if market_service does not answer and
    `ValueError` for any answer but a well-formed `200`.
    """
    # Before the request, as `fetch_suspended` takes it.
    loaded_at = time.monotonic()
    response = await terms_client.get(
        _PUBLISHED_PATH, headers={"X-Service-Key": service_key}
    )
    body = _body_of(response, upstream="market_service")
    return PublishedMarkets(
        terms=dict(_market_of(row) for row in _list_in(body, "markets")),
        loaded_at=loaded_at,
        as_of=_timestamp_of(body.get("as_of"), field="as_of"),
    )


async def refresh_suspended(
    copy: LocalCopy, auth_client: httpx.AsyncClient, *, service_key: str
) -> None:
    """Replace the copy's suspended ids with a fresh fetch, unless the copy
    already holds a newer one. Raises what `fetch_suspended` raises, and then
    the old ids stay."""
    fetched = await fetch_suspended(auth_client, service_key=service_key)
    # Two refreshes may overlap; the one stamped first must not land last and
    # put the copy back in time.
    if copy.suspended is None or fetched.loaded_at > copy.suspended.loaded_at:
        copy.suspended = fetched


async def refresh_published_markets(
    copy: LocalCopy, terms_client: httpx.AsyncClient, *, service_key: str
) -> None:
    """Replace the copy's published markets with a fresh fetch, unless the
    copy already holds a newer one, as `refresh_suspended` does. Raises what
    `fetch_published_markets` raises, and then the old markets stay."""
    fetched = await fetch_published_markets(terms_client, service_key=service_key)
    if copy.markets is None or fetched.loaded_at > copy.markets.loaded_at:
        copy.markets = fetched


async def run_refresher(
    name: str,
    refresh_once: Callable[[], Awaitable[None]],
    *,
    interval_seconds: float,
) -> None:
    """Run `refresh_once`, wait `interval_seconds`, and again, forever.

    Started and cancelled by `main.py`; the first refresh is the first
    iteration, because the copy refuses every trade until it lands. Swallows
    every `Exception`, logged with `name`: see the module docstring.
    `CancelledError` is a `BaseException`, so shutdown still works. Twin of
    `leaderboard_refresher.run_leaderboard_refresher`'s loop.
    """
    logger.info(
        "%s copy refresher started: a refresh now, then every %.1fs",
        name,
        interval_seconds,
    )
    while True:
        with observability.background_run():
            try:
                await refresh_once()
            except Exception:
                logger.exception(
                    "%s copy refresh failed; keeping the old copy, retrying in %.1fs",
                    name,
                    interval_seconds,
                )
        await asyncio.sleep(interval_seconds)


def refuse_if_suspended(copy: LocalCopy, user_id: uuid.UUID, *, now: float) -> None:
    """Raise unless the copy says this user may trade. `now` is
    `time.monotonic()`.

    Raises `AuthUnavailable` for a copy never loaded or past its max age, and
    `AccountSuspended` for a listed id. ADR 0022.
    """
    if not copy.is_suspended_fresh(now=now):
        raise AuthUnavailable
    if user_id in copy.suspended.user_ids:
        raise AccountSuspended


async def ensure_trading(
    session: AsyncSession,
    copy: LocalCopy,
    market_id: uuid.UUID,
    *,
    now: datetime,
    monotonic_now: float,
) -> MarketTradingTerms:
    """This market's terms, if the copy says it may be traded at `now`.

    Raises `MarketTermsUnavailable` for a copy never loaded or past its max
    age. A market absent from it gets one `copy.refresh_markets()`, unless
    the copy is younger than `_MISS_REFRESH_FLOOR_SECONDS`, and the age is
    checked again after it. Still absent, it is `MarketTermsUnavailable` if
    it holds a book (market_service is wrong, ADR 0017) and `MarketNotFound`
    if not. Then `MarketClosed` for any stored status but open, a null close
    time, or one at or before `now`. The caller re-checks `close_time` under
    the book lock.

    **Call this with nothing pending and no row lock held**: a miss that
    refreshes rolls the session back first (D-043). The session is read only
    on a miss, unlocked. A market the copy holds is answered in memory.
    """
    if not copy.is_markets_fresh(now=monotonic_now):
        raise MarketTermsUnavailable

    terms = _terms_in(copy, market_id)
    if terms is None and _is_refresh_due(copy, now=monotonic_now):
        terms = await _terms_after_refresh(session, copy, market_id)
    if terms is None:
        # Unlocked: ADR 0015 locks reads that decide a write, and this one
        # decides only an error code.
        if await books.find(session, market_id) is not None:
            raise MarketTermsUnavailable
        raise MarketNotFound

    if not _is_trading(terms, now=now):
        raise MarketClosed
    return terms
