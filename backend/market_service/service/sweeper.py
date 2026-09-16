"""The background task that notices a market has closed. [F-4] #44.

Deliberately thin, and deliberately not the authority. Everything that decides
whether a market is closed lives in `service/closing.py`; this file owns only
the timer, the session and the promise that a transient database error does not
silently stop markets from ever closing again.

**Why a sweep rather than a scheduled timer per market.** A timer has to be
created when a market is published and cancelled if anything changes, it lives
in one process's memory so it dies with a deploy, and it needs re-arming for
every open market at boot — which is a sweep, run once, plus the bookkeeping.
The sweep is the re-arm step with nothing built on top of it.

**Why not Redis.** `close_time` is already durable and indexed in Postgres, and
ADR 0010 runs Redis with no persistence at all — "the container can be deleted
and recreated at any time and nothing is lost", which is exactly the wrong
property for a schedule. A TTL there would be a second, weaker copy of a value
this service already owns, needing to be kept in step at publish, at [2.3] #7's
early close, and at every edit. Redis expiry notifications are fire-and-forget
besides: nothing is connected, nothing replays, the close never happens. And
the only service that subscribes to Redis must not grow a database (ADR 0010),
so it could not write the status it was being told about.

**What it costs to run.** `ix_markets_due_close` is partial on
`status = 'open'` and keyed on `close_time`, so a sweep is an ordered index
scan over the markets that are still open, stopping at the batch ceiling.
Measured on a table of 20,000 settled and 2,200 open markets, that is 5 index
buffers and 0.05 ms; on the overwhelmingly common tick where nothing is due it
returns no rows in about 4.

For scale, against this application's own traffic: one administrator with the
create form open writes 1,200 autosave transactions an hour, each one deleting
and re-inserting child rows. A sweep every ten seconds is 360 read-only probes
in the same hour. The autosave is the load here; this is not.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from service import closing

logger = logging.getLogger(__name__)

# A backlog is drained in back-to-back batches rather than one per tick, so a
# service that was down for a weekend catches up in seconds instead of in
# `interval * markets` seconds. The cap is a guard against a bug rather than
# against a workload: each batch permanently closes the rows it returns, so a
# correct implementation always terminates. If this limit is ever hit, the log
# line below is the bug report.
MAX_DRAIN_BATCHES = 50


async def run_close_sweeper(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_seconds: float,
    batch_limit: int,
) -> None:
    """Close due markets forever, every `interval_seconds`.

    Started and cancelled by `main.py`. Runs until cancelled, and survives
    anything short of that.

    Sweeps immediately rather than sleeping first, so a service coming back up
    after an outage closes its backlog at once instead of one interval later.

    `asyncio.CancelledError` derives from `BaseException` rather than
    `Exception`, so the handler below lets a shutdown through untouched while
    still swallowing a dropped connection. That is the whole error policy: a
    database hiccup must not be able to end the task, because a task that ended
    is a service where no market ever closes again and nothing says so.
    """
    logger.info(
        "close sweeper started: every %.1fs, up to %d markets per batch",
        interval_seconds,
        batch_limit,
    )
    while True:
        try:
            await _drain(session_factory, batch_limit)
        except Exception:
            logger.exception("close sweep failed; retrying in %.1fs", interval_seconds)
        await asyncio.sleep(interval_seconds)


async def _drain(
    session_factory: async_sessionmaker[AsyncSession], batch_limit: int
) -> int:
    """Close due markets in batches until a batch comes back short. Returns the total.

    A short batch means the index held nothing else due, so there is no reason
    to go round again. A full one means there may be more, and waiting a whole
    interval to find out would leave markets that are already untradeable
    showing as open on a dashboard for no reason.

    One session for the whole drain, one transaction per batch. Those are two
    different boundaries and only the second one is load-bearing: the batch
    ceiling exists to bound how long row locks are held, and
    `closing.close_due_markets` commits at the end of every batch, which is
    what releases them. Re-opening the session between batches would bound
    nothing further, and would check a connection back into a ten-slot pool
    that the three-second autosave is also drawing from — up to fifty times,
    during exactly the backlog this loop exists to clear.
    """
    total = 0
    async with session_factory() as session:
        for _ in range(MAX_DRAIN_BATCHES):
            closed = await closing.close_due_markets(session, limit=batch_limit)

            total += len(closed)
            if closed:
                # At INFO, because a market closing is a real event in the life
                # of the platform and the ids are what makes a later question
                # about one answerable. Silent when nothing was due, which is
                # almost always, so this does not drown the log.
                logger.info(
                    "closed %d market(s) at their closing time: %s", len(closed), closed
                )

            if len(closed) < batch_limit:
                return total

    logger.warning(
        "close sweep stopped after %d full batches; either a real backlog or a "
        "market that is not staying closed",
        MAX_DRAIN_BATCHES,
    )
    return total
