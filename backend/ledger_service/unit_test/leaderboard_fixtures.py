"""Fixtures the [L-1] #38 refresh tests share.

Three files test PR 5: the auth lookup client (`test_user_directory.py`), the
refresh (`test_leaderboard_refresh.py`), and its races
(`test_leaderboard_concurrency.py`). They need the same stand-in auth service,
the same way to read the generations back, and the same way to pause a
refresh at its commit.

The spec is ADR 0021 section 2, `docs/api/auth-service.md`'s
`POST /internal/users/lookup`, and #270's entries in DECISIONS.md. Project
modules are reached through functions, so a missing name fails each test on
its own rather than collection (D-007).

**Every holding comes from a real trade**, and every settled market from real
buys followed by the real `pay_out`. `settlement_fixtures.hold` is not used:
it inserts positions and sets `q` without posting the money for them.

**The oracle is `portfolio_of_user`.** It shares `valuation_of` with the
refresh, so these tests catch mistakes in the refresh's own code (its rows,
its grouping, its balance, its settled handling) and not inside
`valuation_of`, which the portfolio's own suite covers.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from unit_test.conftest import auth_client_over
from unit_test.sell_fixtures import version_of
from unit_test.settlement_fixtures import (
    PROBE_TIMEOUT,
    SettleUpstream,
    settle,
)
from unit_test.settlement_fixtures import warm as warm_settleable
from unit_test.trade_fixtures import buy, entities, fund, session_factory

LOOKUP_PATH = "/internal/users/lookup"

# auth's `/internal/users/lookup` maximum, restated: "between 1 and 500 UUIDs".
LOOKUP_LIMIT = 500

# A fixed instant to stamp generations from, so a test orders them by hand.
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    """`T0` plus `seconds`."""
    return T0 + timedelta(seconds=seconds)


def fixed_clock(instant: datetime) -> Callable[[], datetime]:
    return lambda: instant


# --- lazy module handles --------------------------------------------------
def leaderboard():
    """`service/leaderboard.py`, PR 5's refresh. Does not exist yet."""
    from service import leaderboard  # noqa: PLC0415

    return leaderboard


def user_directory():
    """`service/user_directory.py`, PR 5's auth lookup client. Does not exist yet."""
    from service import user_directory  # noqa: PLC0415

    return user_directory


def service_key() -> str:
    """The key the ledger is configured to send. `ledger_service_key` is new
    in PR 5 (ADR 0021: "the ledger reads the same variable to send")."""
    from core.config import get_settings  # noqa: PLC0415

    return get_settings().ledger_service_key.get_secret_value()


# --- the stand-in auth service --------------------------------------------
class Directory:
    """auth's `POST /internal/users/lookup`, as `docs/api/auth-service.md`
    specifies it.

    It requires the configured `X-Service-Key` (`401 invalid_service_key`
    otherwise), refuses more than 500 ids or any other body field (`422`),
    answers a duplicate once, leaves out an id it does not know, orders by
    `id`, and returns exactly `id`, `username` and `is_suspended`: no role, no
    email, nothing the real route does not send.

    `fail(...)` makes a request answer with a response or raise instead,
    every request or only the n-th (1-based). `on_request` is awaited inside
    every call, which is where a test looks at what the caller holds while
    auth is answering.
    """

    def __init__(self) -> None:
        self.users: dict[uuid.UUID, tuple[str, bool]] = {}
        self.requests: list[httpx.Request] = []
        self.asked: list[list[uuid.UUID]] = []
        self.on_request: Callable[[], Awaitable[None]] | None = None
        self._failures: dict[int | None, httpx.Response | Exception] = {}

    def name(self, user_id: uuid.UUID, username: str, *, suspended: bool = False) -> None:
        self.users[user_id] = (username, suspended)

    def fail(
        self, answer: httpx.Response | Exception, *, on_request: int | None = None
    ) -> None:
        self._failures[on_request] = answer

    @property
    def asked_ids(self) -> set[uuid.UUID]:
        return {user_id for chunk in self.asked for user_id in chunk}

    def _answer(self, ids: list[uuid.UUID]) -> httpx.Response:
        known = sorted({user_id for user_id in ids if user_id in self.users})
        return httpx.Response(
            200,
            json={
                "users": [
                    {
                        "id": str(user_id),
                        "username": self.users[user_id][0],
                        "is_suspended": self.users[user_id][1],
                    }
                    for user_id in known
                ]
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            number = len(self.requests)
            if self.on_request is not None:
                await self.on_request()
            if (request.method, request.url.path) != ("POST", LOOKUP_PATH):
                raise AssertionError(f"unexpected call {request.method} {request.url}")

            if request.headers.get("X-Service-Key") != service_key():
                return httpx.Response(
                    401,
                    json={
                        "error": {
                            "code": "invalid_service_key",
                            "message": "A valid service key is required.",
                        }
                    },
                )
            body = json.loads(request.content)
            if not isinstance(body, dict) or set(body) != {"user_ids"}:
                return httpx.Response(422, json={"detail": [{"msg": "extra fields"}]})
            ids = [uuid.UUID(raw) for raw in body["user_ids"]]
            if not 1 <= len(ids) <= LOOKUP_LIMIT:
                return httpx.Response(422, json={"detail": [{"msg": "1 to 500 ids"}]})
            self.asked.append(ids)

            failure = self._failures.get(number, self._failures.get(None))
            if isinstance(failure, Exception):
                raise failure
            if failure is not None:
                return failure
            return self._answer(ids)

        return httpx.MockTransport(handler)


# --- driving the things under test ----------------------------------------
async def list_users(user_ids: Sequence[uuid.UUID], directory: Directory):
    """The lookup, with the signature ruled for PR 5: `list_users(user_ids,
    auth_client)`, no keyword-only marker (backend/CLAUDE.md:71-72: the two
    arguments do not share a type). The single place it is written down."""
    async with auth_client_over(directory.transport) as auth_client:
        return await user_directory().list_users(list(user_ids), auth_client)


async def refresh_with(
    directory: Directory,
    *,
    clock: Callable[[], datetime] | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """One refresh, with the signature ruled for PR 5: `refresh(session_factory,
    auth_client, clock=...)`. The production auth client sits over the
    directory's transport, so its base URL and timeout still run. Without a
    `clock`, the refresh's own default reads the time. The single place it is
    written down."""
    extra = {} if clock is None else {"clock": clock}
    async with auth_client_over(directory.transport) as auth_client:
        await leaderboard().refresh(
            factory if factory is not None else session_factory(), auth_client, **extra
        )


# --- holders --------------------------------------------------------------
async def granted(session: AsyncSession, directory: Directory, username: str) -> uuid.UUID:
    """A user with their starting credits and nothing else, named in auth."""
    user_id = uuid.uuid4()
    await fund(session, user_id)
    directory.name(user_id, username)
    return user_id


async def settled_by_trading(
    session: AsyncSession, buys: Sequence[tuple[uuid.UUID, int, Decimal]]
) -> SettleUpstream:
    """A market opened at zero, traded into by real buys `(user, outcome
    position, quantity)`, then settled for `settlement_fixtures.WINNER` by the
    real `pay_out`. Every share was paid for and every payout posted."""
    upstream = SettleUpstream()
    await warm_settleable(session, upstream)
    for user_id, outcome, quantity in buys:
        await buy(
            session,
            upstream,
            user_id=user_id,
            outcome=outcome,
            quantity=quantity,
            state_version=await version_of(session, upstream.market_id),
            client_key=f"before-settlement-{uuid.uuid4()}",
            transport=upstream.gate_open,
        )
    await settle(session, upstream)
    return upstream


async def net_worths_from_portfolio(
    session: AsyncSession, user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """The oracle: each user's `portfolio_of_user(...).net_worth`."""
    from service import portfolio  # noqa: PLC0415

    worths: dict[uuid.UUID, Decimal] = {}
    for user_id in user_ids:
        worths[user_id] = (await portfolio.portfolio_of_user(session, user_id)).net_worth
    await session.commit()
    return worths


# --- reading the generations back ---------------------------------------
@dataclass(frozen=True)
class Entry:
    rank: int
    user_id: uuid.UUID
    username: str
    net_worth: Decimal


@dataclass(frozen=True)
class Generation:
    id: uuid.UUID
    computed_at: datetime
    ranked_users: int
    entries: tuple[Entry, ...]

    def by_user(self) -> dict[uuid.UUID, Entry]:
        return {entry.user_id: entry for entry in self.entries}

    @property
    def is_whole(self) -> bool:
        return self.ranked_users == len(self.entries)


async def _generations_in(
    session: AsyncSession, ids: set[uuid.UUID] | None = None
) -> list[Generation]:
    ents = entities()
    generation_table = ents.LeaderboardGeneration
    entry_table = ents.LeaderboardEntry
    stmt = select(
        generation_table.id, generation_table.computed_at, generation_table.ranked_users
    ).order_by(generation_table.computed_at)
    if ids is not None:
        stmt = stmt.where(generation_table.id.in_(ids))
    generations: list[Generation] = []
    for generation_id, computed_at, ranked_users in (await session.execute(stmt)).all():
        rows = (
            await session.execute(
                select(
                    entry_table.rank,
                    entry_table.user_id,
                    entry_table.username,
                    entry_table.net_worth,
                )
                .where(entry_table.generation_id == generation_id)
                .order_by(entry_table.rank)
            )
        ).all()
        generations.append(
            Generation(
                id=generation_id,
                computed_at=computed_at,
                ranked_users=ranked_users,
                entries=tuple(Entry(*row) for row in rows),
            )
        )
    return generations


async def generations() -> list[Generation]:
    """Every committed generation, oldest first, from a session of its own."""
    async with session_factory()() as reader:
        return await _generations_in(reader)


async def newest() -> Generation | None:
    """The current ranking as the contract defines it: the newest
    `computed_at`. There is no read route yet, so this is the reader."""
    every = await generations()
    return every[-1] if every else None


# --- pausing a refresh at its commit ---------------------------------------
class CommitPause:
    """Runs at every commit a refresh's sessions make, before the real commit.

    At each one it records what a reader in another session sees as the
    newest generation. At the first commit whose session holds a generation
    no other session can see yet (the write commit), it records that
    generation as the refresh's own session sees it, sets `reached` and, if
    pausing, waits for `release`. The xact advisory lock is still held then.

    Assumes the refresh commits with `await session.commit()`. One that used
    `async with session.begin()` would never reach this, and the test fails
    with "never reached", not silently.
    """

    def __init__(self, *, pause: bool = True) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        if not pause:
            self.release.set()
        self.own_view: Generation | None = None
        self.reader_views: list[Generation | None] = []

    async def on_commit(self, session: AsyncSession) -> None:
        # What `commit` would flush anyway, so pending ORM rows are visible
        # to the select below.
        await session.flush()
        own_ids = set(
            (await session.execute(select(entities().LeaderboardGeneration.id))).scalars()
        )
        committed_ids = {generation.id for generation in await generations()}
        self.reader_views.append(await newest())

        uncommitted = own_ids - committed_ids
        if uncommitted and self.own_view is None:
            [self.own_view] = await _generations_in(session, uncommitted)
            self.reached.set()
            await self.release.wait()


def pausing_factory(pause: CommitPause) -> async_sessionmaker[AsyncSession]:
    """The production factory's settings, with `pause.on_commit` run before
    every commit."""
    from core.database import get_engine  # noqa: PLC0415

    class PausingSession(AsyncSession):
        async def commit(self) -> None:
            await pause.on_commit(self)
            await super().commit()

    return async_sessionmaker(
        get_engine(), class_=PausingSession, expire_on_commit=False, autoflush=False
    )


_ADVISORY_LOCK_WAITERS = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() "
    "AND usename = current_user "
    "AND wait_event_type = 'Lock' "
    "AND wait_event = 'advisory' "
    "AND pid <> pg_backend_pid()"
)


async def advisory_lock_waiters() -> int:
    """Backends of this role queued on an advisory lock, from Postgres's own
    view. A fresh connection per call, because `pg_stat_activity` is
    snapshotted once per transaction."""
    from core.database import get_engine  # noqa: PLC0415

    async with get_engine().connect() as conn:
        return (await conn.execute(_ADVISORY_LOCK_WAITERS)).scalar_one()


async def queued_on_the_advisory_lock(task: asyncio.Task, what: str) -> bool:
    """Wait until one backend is queued on an advisory lock (True), or `task`
    has finished without queueing (False). Fails naming `what` if neither
    happens. Polled, but the answer does not depend on how fast anything is."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROBE_TIMEOUT
    while True:
        if task.done():
            return False
        if await advisory_lock_waiters() == 1:
            return True
        if loop.time() > deadline:
            pytest.fail(f"timed out: {what}")
        await asyncio.sleep(0.01)
