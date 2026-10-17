"""The leaderboard refresh loop on the app lifespan. [L-3] #40, ADR 0021.

DECISIONS.md, "The refresh runs every 60 seconds in a loop on each replica,
behind `LEADERBOARD_REFRESH_ENABLED`, and a failed refresh writes nothing":
`main.py`'s lifespan starts one loop when the setting is true, the loop runs a
refresh at once, and shutdown stops it. The conftest turns the loop off, so
each test here turns it on, or off, itself.

Nothing here waits on time to decide an answer. The refresh is replaced by a
stand-in that marks each call with an `asyncio.Event`, or wrapped so it blocks
on one; `PROBE_TIMEOUT` only turns a hang into a failure.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from unit_test.conftest import bearer
from unit_test.leaderboard_read_fixtures import PATH, store_generation
from unit_test.settlement_fixtures import PROBE_TIMEOUT, reached
from unit_test.trade_fixtures import fund

_TASK_NAME = "leaderboard-refresh"
_MAIN_LOGGER = "main"

# A port nothing listens on, so auth refuses at once rather than not answering.
_UNREACHABLE_AUTH = "http://127.0.0.1:9"


def _leaderboard():
    from service import leaderboard  # noqa: PLC0415

    return leaderboard


def _errors():
    from core import errors  # noqa: PLC0415

    return errors


def _app() -> FastAPI:
    from main import create_app  # noqa: PLC0415

    return create_app()


def _refresh_enabled(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setattr(get_settings(), "leaderboard_refresh_enabled", enabled)


def _refresh_tasks() -> list[asyncio.Task]:
    return [task for task in asyncio.all_tasks() if task.get_name() == _TASK_NAME]


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


async def test_the_app_boots_and_serves_with_auth_unreachable(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[L-3] #40: "A leaderboard refresh failure retains the previous valid
    ranking". The lifespan yields without waiting for the first refresh, so
    the app boots and serves while that refresh is still running and auth is
    unreachable.

    The real refresh is wrapped to block before it starts. While it is
    blocked the app answers `/health` and serves the stored ranking. Released,
    it fails on auth, which refuses: one holder is funded, so the refresh has
    somebody to name and does call auth.

    Catches: the lifespan awaiting the first refresh before `yield`, bare or
    inside `try/except`. Either way the lifespan never yields while the
    refresh is held, and `_running` fails.
    """
    await fund(session, uuid.uuid4())
    computed_at = datetime.now(UTC)
    await store_generation(computed_at=computed_at, rows=[("ann", Decimal("12.3400"))])
    _refresh_enabled(monkeypatch, True)
    monkeypatch.setattr(get_settings(), "auth_service_url", _UNREACHABLE_AUTH)

    real_refresh = _leaderboard().refresh
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    raised: list[BaseException] = []

    async def held_refresh(*args, **kwargs) -> None:
        entered.set()
        await release.wait()
        try:
            await real_refresh(*args, **kwargs)
        except Exception as exc:
            raised.append(exc)
            raise
        finally:
            finished.set()

    monkeypatch.setattr(_leaderboard(), "refresh", held_refresh)
    app = _app()

    async with _running(app):
        await reached(entered, "the loop never called the refresh")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            health = await client.get("/health")
            board = await client.get(PATH, headers=bearer(uuid.uuid4()))

        assert not finished.is_set(), "the refresh ran before the app served"
        assert health.status_code == 200
        assert board.status_code == 200, board.text
        body = board.json()
        assert datetime.fromisoformat(body["computed_at"].replace("Z", "+00:00")) == computed_at
        assert body["rows"] == [
            {"rank": 1, "username": "ann", "net_worth": "12.3400", "is_me": False}
        ]

        release.set()
        await reached(finished, "the released refresh never finished")

    assert len(raised) == 1
    assert isinstance(raised[0], _errors().AuthUnavailable), repr(raised[0])


async def test_the_first_refresh_runs_at_startup_not_after_one_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"The loop runs a refresh at once, then one every
    `LEADERBOARD_REFRESH_SECONDS`." The interval is an hour, so only a refresh
    at startup reaches the stand-in inside the probe's bound. Catches: the
    loop sleeping before its first refresh."""
    _refresh_enabled(monkeypatch, True)
    monkeypatch.setattr(get_settings(), "leaderboard_refresh_seconds", 3600)
    called = asyncio.Event()

    async def stand_in(*args, **kwargs) -> None:
        called.set()

    monkeypatch.setattr(_leaderboard(), "refresh", stand_in)
    app = _app()

    async with _running(app):
        await reached(called, "no refresh ran at startup")


async def test_disabled_starts_no_refresh_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LEADERBOARD_REFRESH_ENABLED=false` stops this replica refreshing.
    Catches: the setting not gating the task. The name is the one the shutdown
    test finds the task by, so a renamed task fails that test rather than
    passing this one quietly."""
    _refresh_enabled(monkeypatch, False)
    calls: list[object] = []

    async def stand_in(*args, **kwargs) -> None:
        calls.append(args)

    monkeypatch.setattr(_leaderboard(), "refresh", stand_in)
    app = _app()

    async with _running(app):
        assert _refresh_tasks() == []

    assert calls == []


async def test_disabled_logs_a_warning_naming_the_setting(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A replica with the loop off says so at startup, naming the setting, so
    a board that is going stale because every replica was switched off is
    traceable from the logs. Level and setting name only, never the full text.

    Catches: the warning removed, or logged at INFO.
    """
    _refresh_enabled(monkeypatch, False)
    caplog.set_level(logging.INFO, logger=_MAIN_LOGGER)
    app = _app()

    async with _running(app):
        pass

    warnings = [
        record
        for record in caplog.records
        if record.name == _MAIN_LOGGER and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1, f"expected one WARNING from {_MAIN_LOGGER!r}, found {warnings}"
    assert "LEADERBOARD_REFRESH_ENABLED" in warnings[0].getMessage()


async def test_shutdown_cancels_the_refresh_before_the_auth_client_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown cancels the loop and awaits it, inside the block that holds
    the auth client, so a refresh in flight never calls auth on a closed
    client and no task outlives the app. The stand-in is mid-refresh at
    shutdown and records whether its client was still open when it was
    cancelled.

    Catches: the cancel moved past the `async with` that closes the auth
    client. Also red with no cancel at all: the task is still pending.
    """
    _refresh_enabled(monkeypatch, True)
    entered = asyncio.Event()
    closed_when_cancelled: list[bool] = []

    async def mid_refresh(session_factory, auth_client, *args, **kwargs) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            closed_when_cancelled.append(auth_client.is_closed)
            raise

    monkeypatch.setattr(_leaderboard(), "refresh", mid_refresh)
    app = _app()

    async with _running(app):
        await reached(entered, "the loop never called the refresh")
        tasks = _refresh_tasks()
        assert len(tasks) == 1, f"expected one task named {_TASK_NAME!r}, found {tasks}"
        [task] = tasks

    assert task.done(), "the refresh task outlived the app"
    assert task.cancelled()
    assert closed_when_cancelled == [False], (
        "the refresh was cancelled after the auth client closed, or never"
    )
