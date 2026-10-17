"""The leaderboard refresh loop, driven without the app. [L-3] #40.

DECISIONS.md, "The refresh runs every 60 seconds in a loop on each replica":
"The loop catches every `Exception`, logs it at ERROR and waits for the next
tick." ADR 0011 gives the reason, for the close sweep: a task that has died
is a feature that has stopped with nobody told.

The refresh is replaced by a stand-in that raises on its first call and marks
its second with an `asyncio.Event`. The interval is the loop's own argument,
kept tiny so the suite is quick; no answer depends on it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

import pytest

from unit_test.settlement_fixtures import reached

_TICK = 0.001


def _leaderboard():
    from service import leaderboard  # noqa: PLC0415

    return leaderboard


def _refresher():
    """`service/leaderboard_refresher.py`."""
    from service import leaderboard_refresher  # noqa: PLC0415

    return leaderboard_refresher


class _FailsThenWaits:
    """Raises `failure` on the first call, sets `second_call` on the second and
    then waits until cancelled."""

    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.calls = 0
        self.second_call = asyncio.Event()

    async def __call__(self, *args, **kwargs) -> None:
        self.calls += 1
        if self.calls == 1:
            raise self.failure
        self.second_call.set()
        await asyncio.Event().wait()


class _SlowRefresh:
    """Takes `duration` seconds per call and records when each call started and
    ended. `third_call_started` is set as the third call begins."""

    def __init__(self, duration: float) -> None:
        self.duration = duration
        self.started_at: list[float] = []
        self.ended_at: list[float] = []
        self.third_call_started = asyncio.Event()

    async def __call__(self, *args, **kwargs) -> None:
        self.started_at.append(time.monotonic())
        if len(self.started_at) == 3:
            self.third_call_started.set()
        await asyncio.sleep(self.duration)
        self.ended_at.append(time.monotonic())


@contextlib.asynccontextmanager
async def _loop_running(
    monkeypatch: pytest.MonkeyPatch,
    stand_in: Callable[..., Awaitable[None]],
    *,
    interval_seconds: float = _TICK,
):
    """The loop on a task, over the stand-in, cancelled and awaited on exit."""
    monkeypatch.setattr(_leaderboard(), "refresh", stand_in)
    task = asyncio.create_task(
        _refresher().run_leaderboard_refresher(
            object(), object(), interval_seconds=interval_seconds
        )
    )
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_a_refresh_that_raises_does_not_stop_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"The loop catches every `Exception` ... and waits for the next tick."
    A `RuntimeError`, not a domain error, because the bug is a handler
    narrowed to the errors somebody expected. Catches: `except
    AuthUnavailable` (or no handler): the task dies on the first call and
    `reached` raises its error."""
    stand_in = _FailsThenWaits(RuntimeError("not a domain error"))

    async with _loop_running(monkeypatch, stand_in) as task:
        await reached(stand_in.second_call, "the loop never refreshed again", [task])


async def test_a_refresh_that_raises_is_logged_at_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """"... logs it at ERROR", with the exception attached, so a refresh
    failing every minute is visible in the logs. Matched by the exception
    itself, never by the message's text. Catches: `except Exception: pass`."""
    failure = RuntimeError("not a domain error")
    stand_in = _FailsThenWaits(failure)
    caplog.set_level(logging.INFO)

    async with _loop_running(monkeypatch, stand_in) as task:
        await reached(stand_in.second_call, "the loop never refreshed again", [task])

    logged = [
        record
        for record in caplog.records
        if record.exc_info is not None and record.exc_info[1] is failure
    ]
    assert [record.levelno for record in logged] == [logging.ERROR]


async def test_the_refresher_sleeps_after_each_refresh_not_at_a_fixed_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop "sleeps after each refresh rather than at a fixed rate": a
    refresh slower than the interval is followed by a full interval, never by
    an immediate next one. The stand-in takes four times the interval; the
    third call starting means two full refresh-then-sleep cycles completed.
    Catches: a sleep that subtracts the refresh's duration."""
    interval_seconds = 0.05
    stand_in = _SlowRefresh(duration=0.2)

    async with _loop_running(
        monkeypatch, stand_in, interval_seconds=interval_seconds
    ) as task:
        await reached(
            stand_in.third_call_started, "the loop never refreshed a third time", [task]
        )

    gaps = [
        started - ended
        for started, ended in zip(stand_in.started_at[1:], stand_in.ended_at)
    ]
    assert len(gaps) == 2
    assert all(gap >= interval_seconds for gap in gaps)
