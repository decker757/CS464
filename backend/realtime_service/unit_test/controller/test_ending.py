"""How a connection decides which of several simultaneous failures closed it.

`_serve` waits on four tasks and more than one can finish in the same tick, so
`_ending` picks between their exceptions. A set has no iteration order, which
makes "whichever we see first" a coin toss on object hashing rather than a
rule — these tests are what stop that coin toss coming back.
"""

from __future__ import annotations

import asyncio

from starlette.websockets import WebSocketDisconnect

from controller.routes import _ending
from core.errors import SessionExpired, SlowConsumer


async def _finished(*exceptions: BaseException | None) -> set[asyncio.Task]:
    """Tasks that have already finished, one per exception (None = returned)."""

    async def run(exc: BaseException | None) -> None:
        if exc is not None:
            raise exc

    tasks = {asyncio.create_task(run(exc)) for exc in exceptions}
    await asyncio.gather(*tasks, return_exceptions=True)
    return tasks


async def test_a_domain_reason_wins_over_an_unexpected_failure() -> None:
    """The real case: a client vanishes, the reader raises WebSocketDisconnect
    and the pump's concurrent send raises Starlette's "cannot send once a close
    message has been sent" RuntimeError. The token expiring at the same moment
    must still be what the client is told about."""
    for _ in range(20):
        done = await _finished(SessionExpired(), RuntimeError("send after close"))

        assert _ending(done) == (
            SessionExpired.close_code,
            SessionExpired.message,
        )


async def test_an_unexpected_failure_wins_over_a_disconnect() -> None:
    """A disconnect is the ordinary ending and says nothing. If something else
    also broke, that is the thing worth logging and reporting."""
    for _ in range(20):
        done = await _finished(WebSocketDisconnect(1000), RuntimeError("boom"))

        assert _ending(done) == (1011, "Internal error.")


async def test_the_first_domain_reason_is_reported_whichever_task_holds_it() -> None:
    """Two domain reasons at once is a tie this does not try to break — but it
    must resolve to one of them rather than to an internal error."""
    for _ in range(20):
        done = await _finished(SessionExpired(), SlowConsumer())

        code, _reason = _ending(done)
        assert code in {SessionExpired.close_code, SlowConsumer.close_code}


async def test_a_plain_disconnect_closes_nothing() -> None:
    """There is nobody left to send a close frame to."""
    done = await _finished(WebSocketDisconnect(1000))

    assert _ending(done) is None


async def test_a_task_returning_cleanly_is_an_ordinary_close() -> None:
    done = await _finished(None)

    assert _ending(done) == (1000, "")


async def test_a_cancelled_task_is_not_an_ending() -> None:
    """Cancelled siblings are the normal shape of every teardown, and asking a
    cancelled task for its exception raises rather than returning one."""
    cancelled = asyncio.create_task(asyncio.Event().wait())
    cancelled.cancel()
    await asyncio.gather(cancelled, return_exceptions=True)

    assert _ending({cancelled}) == (1000, "")
