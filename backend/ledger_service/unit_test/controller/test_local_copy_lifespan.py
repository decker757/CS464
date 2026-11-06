"""The local copy on the app lifespan. #640, ADR 0030.

`main.py`'s lifespan builds the copy, starts one refresher per half and
yields without waiting for either, so the app boots with auth and
market_service down. Until a half's first refresh lands, `/ready` names it
down and every trade refuses 503 before it writes anything. Shutdown cancels
both refreshers while the clients they call are still open.

Nothing here waits on time to decide an answer. The fetches are replaced by
stand-ins that block on an `asyncio.Event`; `PROBE_TIMEOUT` only turns a hang
into a failure. Shaped like `test_leaderboard_refresh_lifespan.py`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from unit_test.conftest import bearer
from unit_test.trade_fixtures import PROBE_TIMEOUT, reached

_SUSPENDED_TASK = "local-copy-suspended"
_MARKETS_TASK = "local-copy-markets"

# A port nothing listens on, so a fetch is refused at once rather than not
# answered.
_UNREACHABLE = "http://127.0.0.1:9"


def _local_copy():
    from service import local_copy  # noqa: PLC0415

    return local_copy


def _app() -> FastAPI:
    from main import create_app  # noqa: PLC0415

    return create_app()


def _both_upstreams_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "auth_service_url", _UNREACHABLE)
    monkeypatch.setattr(get_settings(), "market_service_url", _UNREACHABLE)


def _tasks_named(name: str) -> list[asyncio.Task]:
    return [task for task in asyncio.all_tasks() if task.get_name() == name]


@asynccontextmanager
async def _running(app: FastAPI) -> AsyncIterator[None]:
    """The app's lifespan, entered with a bound. A lifespan that does not
    yield fails here on an assertion, not by hanging the suite."""
    lifespan = app.router.lifespan_context(app)
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            await lifespan.__aenter__()
    except TimeoutError:
        pytest.fail("timed out: the lifespan never yielded, so the app never served")
    try:
        yield
    finally:
        await lifespan.__aexit__(None, None, None)


@asynccontextmanager
async def _http(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


class _HeldFetch:
    """A stand-in for one of `local_copy`'s two fetches: blocks until
    `release` is set, then answers an empty, well-formed snapshot stamped at
    that moment, as a fetch that succeeded would. `called` counts the calls,
    and `entered` is set as the first begins."""

    def __init__(self, *, snapshot_of) -> None:
        self.snapshot_of = snapshot_of
        self.called = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, client, *, service_key: str):
        self.called += 1
        self.entered.set()
        await self.release.wait()
        return self.snapshot_of(time.monotonic())


def _held_suspended_fetch(monkeypatch: pytest.MonkeyPatch) -> _HeldFetch:
    def nobody_suspended(loaded_at: float):
        return _local_copy().SuspendedUsers(
            user_ids=frozenset(), loaded_at=loaded_at, as_of=datetime.now(UTC)
        )

    fetch = _HeldFetch(snapshot_of=nobody_suspended)
    monkeypatch.setattr(_local_copy(), "fetch_suspended", fetch)
    return fetch


def _held_markets_fetch(monkeypatch: pytest.MonkeyPatch) -> _HeldFetch:
    def no_markets(loaded_at: float):
        return _local_copy().PublishedMarkets(
            terms={}, loaded_at=loaded_at, as_of=datetime.now(UTC)
        )

    fetch = _HeldFetch(snapshot_of=no_markets)
    monkeypatch.setattr(_local_copy(), "fetch_published_markets", fetch)
    return fetch


async def _until(condition, what: str) -> None:
    """Wait for `condition()` to hold, or fail naming `what`."""
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            while not condition():
                await asyncio.sleep(0.01)
    except TimeoutError:
        pytest.fail(f"timed out: {what}")


async def test_the_app_boots_and_health_answers_with_auth_and_market_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lifespan yields without waiting for either first refresh, so a
    replica boots while auth and market_service are down. The real refreshes
    are wrapped to block before they start; while both are blocked the app
    answers `/health`. Released, each fails on its refused connection, and
    the copy stays empty.

    Catches: the lifespan awaiting either first refresh before `yield`, bare
    or inside `try/except`: it never yields while the refresh is held.
    """
    _both_upstreams_unreachable(monkeypatch)
    local_copy = _local_copy()
    release = asyncio.Event()
    entered = {"suspended": asyncio.Event(), "markets": asyncio.Event()}
    raised: dict[str, BaseException] = {}

    def held(name: str, real_refresh):
        async def held_refresh(*args, **kwargs) -> None:
            entered[name].set()
            await release.wait()
            try:
                await real_refresh(*args, **kwargs)
            except Exception as exc:
                raised[name] = exc
                raise

        return held_refresh

    monkeypatch.setattr(
        local_copy, "refresh_suspended", held("suspended", local_copy.refresh_suspended)
    )
    monkeypatch.setattr(
        local_copy,
        "refresh_published_markets",
        held("markets", local_copy.refresh_published_markets),
    )
    app = _app()

    async with _running(app):
        await reached(entered["suspended"], "the suspended refresher never ran")
        await reached(entered["markets"], "the markets refresher never ran")
        async with _http(app) as client:
            health = await client.get("/health")
        assert health.status_code == 200

        release.set()
        await _until(lambda: len(raised) == 2, "the released refreshes never failed")
        copy = app.state.local_copy
        assert copy.suspended is None
        assert copy.markets is None

    assert set(raised) == {"suspended", "markets"}
    for exc in raised.values():
        assert isinstance(exc, httpx.HTTPError), repr(exc)


async def test_ready_says_the_copies_are_down_until_the_first_refresh_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0030: a replica whose copy never loaded refuses every trade, so
    `/ready` names each half down until its first refresh lands, and only
    then answers 200. The halves are released one at a time, so each check
    is seen to read its own half.

    Catches: either check missing, reading the other half, or answering
    from the copy's existence rather than its freshness.
    """
    suspended_fetch = _held_suspended_fetch(monkeypatch)
    markets_fetch = _held_markets_fetch(monkeypatch)
    app = _app()

    async with _running(app), _http(app) as client:
        copy = app.state.local_copy
        await reached(suspended_fetch.entered, "the suspended refresher never fetched")
        await reached(markets_fetch.entered, "the markets refresher never fetched")

        before = await client.get("/ready")
        assert before.status_code == 503
        assert before.json()["dependencies"] == {
            "database": "ok",
            "suspended_copy": "down",
            "market_copy": "down",
        }

        suspended_fetch.release.set()
        await _until(lambda: copy.suspended is not None, "the suspended half never landed")
        halfway = await client.get("/ready")
        assert halfway.status_code == 503
        assert halfway.json()["dependencies"] == {
            "database": "ok",
            "suspended_copy": "ok",
            "market_copy": "down",
        }

        markets_fetch.release.set()
        await _until(lambda: copy.markets is not None, "the markets half never landed")
        after = await client.get("/ready")
        assert after.status_code == 200
        assert after.json()["dependencies"] == {
            "database": "ok",
            "suspended_copy": "ok",
            "market_copy": "ok",
        }


async def test_a_trade_before_the_first_refresh_is_503_not_executed(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy that has not loaded is a dependency that is not answering, so a
    trade fails closed with that dependency's code and writes nothing (ADR
    0017, ADR 0022, ADR 0030). The suspended half is released first, so the
    second trade reaches the market half and is refused there.

    Catches: the lifespan not setting `app.state.local_copy` (a 500), or a
    copy that trades before it loads. The empty ledger shows neither trade
    got as far as the grant.
    """
    from model.entities import Transaction  # noqa: PLC0415

    suspended_fetch = _held_suspended_fetch(monkeypatch)
    markets_fetch = _held_markets_fetch(monkeypatch)
    user_id = uuid.uuid4()
    body = {
        "outcome_id": str(uuid.uuid4()),
        "side": "buy",
        "quantity": "1",
        "state_version": 0,
        "idempotency_key": "before-the-first-refresh",
    }
    path = f"/ledger/markets/{uuid.uuid4()}/trades"
    app = _app()

    async with _running(app), _http(app) as client:
        await reached(suspended_fetch.entered, "the suspended refresher never fetched")
        await reached(markets_fetch.entered, "the markets refresher never fetched")

        neither_loaded = await client.post(path, json=body, headers=bearer(user_id))
        assert neither_loaded.status_code == 503, neither_loaded.text
        assert neither_loaded.json()["error"]["code"] == "auth_unavailable"

        suspended_fetch.release.set()
        copy = app.state.local_copy
        await _until(lambda: copy.suspended is not None, "the suspended half never landed")
        markets_not_loaded = await client.post(path, json=body, headers=bearer(user_id))
        assert markets_not_loaded.status_code == 503, markets_not_loaded.text
        assert markets_not_loaded.json()["error"]["code"] == "market_terms_unavailable"

        markets_fetch.release.set()

    written = await session.scalar(select(func.count()).select_from(Transaction))
    assert written == 0


async def test_a_miss_during_the_background_refresh_shares_its_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The markets refresher and a trade's miss share one coalesced refresh,
    so the two never fetch the published list at once. The background fetch
    is held; a miss arriving meanwhile waits for it rather than starting
    another, and both end with one fetch made.

    Catches: `copy.refresh_markets` built as a second `coalesced` wrapper,
    or not as one at all: either fetches twice.
    """
    _held_suspended_fetch(monkeypatch)
    markets_fetch = _held_markets_fetch(monkeypatch)
    app = _app()

    async with _running(app):
        copy = app.state.local_copy
        await reached(markets_fetch.entered, "the markets refresher never fetched")

        miss = asyncio.create_task(copy.refresh_markets())
        # Time only lets the miss queue; a miss that has not queued yet runs
        # its own fetch and fails red, never green.
        await asyncio.sleep(0.05)
        markets_fetch.release.set()
        async with asyncio.timeout(PROBE_TIMEOUT):
            await miss

        assert markets_fetch.called == 1


async def test_shutdown_cancels_both_refreshers_before_the_clients_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown cancels both loops and awaits them, inside the block that
    holds the auth and market_service clients, so a refresh in flight never
    calls a closed client and no task outlives the app. Each stand-in is
    mid-refresh at shutdown and records whether its client was still open
    when it was cancelled.

    Catches: either cancel moved past the `async with` that closes the
    clients. Also red with no cancel at all: the task is still pending.
    """
    local_copy = _local_copy()
    entered = {"suspended": asyncio.Event(), "markets": asyncio.Event()}
    closed_when_cancelled: dict[str, bool] = {}

    def mid_refresh(name: str):
        async def stand_in(copy, client, *, service_key: str) -> None:
            entered[name].set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                closed_when_cancelled[name] = client.is_closed
                raise

        return stand_in

    monkeypatch.setattr(local_copy, "refresh_suspended", mid_refresh("suspended"))
    monkeypatch.setattr(local_copy, "refresh_published_markets", mid_refresh("markets"))
    app = _app()

    async with _running(app):
        await reached(entered["suspended"], "the suspended refresher never ran")
        await reached(entered["markets"], "the markets refresher never ran")
        tasks = _tasks_named(_SUSPENDED_TASK) + _tasks_named(_MARKETS_TASK)
        assert [task.get_name() for task in tasks] == [_SUSPENDED_TASK, _MARKETS_TASK]

    for task in tasks:
        assert task.done(), f"{task.get_name()} outlived the app"
        assert task.cancelled()
    assert closed_when_cancelled == {"suspended": False, "markets": False}, (
        "a refresher was cancelled after its client closed, or never"
    )
