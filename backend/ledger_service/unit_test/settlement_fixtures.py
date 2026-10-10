"""Fixtures the [3.4] #12 settlement tests share.

Three files test PR 5: the settlement service, its races, and the trade
latch. They need the same stand-in market_service, which answers both the
public detail and `POST /markets/{id}/settle`, the same holdings, and the same
ways to pause one session and watch another queue behind it.

The spec is DECISIONS.md's 2026-10-10 entries, ADR 0019 as amended, and #12's
criteria and test notes. Project modules are reached through functions, so a
missing name fails each test on its own rather than collection (D-007).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from unit_test.conftest import mint_token, terms_client_over
from unit_test.trade_fixtures import (
    accounts_module,
    books,
    entities,
    session_factory,
    set_q,
)

QUANTUM = Decimal("0.0001")
ZERO = Decimal(0)

# The seed every warm book here starts with. The pool is this until a trade
# moves it, so a residue is this minus what the winners hold.
SUBSIDY = Decimal("250.0000")
B = Decimal("137.0000")

# How long a probe waits for the state it needs before failing by name.
# Everything here finishes in milliseconds; this only stops a hang.
PROBE_TIMEOUT = 10.0

WINNER = 1
LOSER = 0

# [3.4] #12's PR 7 reads: a winning and a losing holding, neither a round
# number, so a figure rounded or rescaled on the way out shows.
PAID = Decimal("41.3337")
UNPAID = Decimal("17.2909")


# --- lazy module handles --------------------------------------------------
def settlement():
    """`service/settlement.py`, PR 5's own module. Does not exist yet."""
    from service import settlement  # noqa: PLC0415

    return settlement


# --- the actor ------------------------------------------------------------
def admin(username: str = "ernest_t"):
    """A distinct administrator. A fresh id every call: the audit log cannot be
    cleaned, so a test sees only its own entries by filtering on an id nothing
    else has used (ADR 0006). `shared.audit.Actor` is the class the ledger's
    `core/audit.py` re-exports, as market_service's does."""
    from shared.audit import Actor  # noqa: PLC0415

    return Actor(id=uuid.uuid4(), username=username, role=UserRole.ADMIN.value)


# --- the market service -----------------------------------------------------
Hook = Callable[[], Awaitable[None]]


class SettleUpstream:
    """A stand-in market_service for settlement: the public detail and the
    settle step, counted apart.

    Approved, settleable, with outcome `WINNER` proposed, unless a test says
    otherwise. `on_get` and `on_post` take the 1-based number of that call and
    may await, which is where a test stalls a call or watches what the caller
    holds while it is in flight.
    """

    def __init__(
        self,
        *,
        status: str = "approved",
        settleable: bool = True,
        outcomes: int = 2,
    ) -> None:
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]
        self.gets = 0
        self.posts = 0
        self.post_tokens: list[str] = []
        self.get_status_code = 200
        self.post_status_code = 200
        self.get_raises: Exception | None = None
        self.post_raises: Exception | None = None
        self.on_get: Callable[[int], Awaitable[None]] | None = None
        self.on_post: Callable[[int], Awaitable[None]] | None = None
        self.body: dict[str, Any] = {
            "id": str(self.market_id),
            "status": status,
            "close_time": "2026-09-30T12:00:00Z",
            "resolution_time": "2026-10-01T12:00:00Z",
            "liquidity_b": str(B),
            "seed_subsidy": str(SUBSIDY),
            "published_at": "2026-09-01T09:00:00Z",
            "proposed_outcome_id": str(self.outcomes[WINNER]),
            "settleable": settleable,
            "outcomes": [
                {"id": str(o), "position": i, "label": f"Outcome {i}"}
                for i, o in enumerate(self.outcomes)
            ],
        }

    @property
    def winner(self) -> uuid.UUID:
        return self.outcomes[WINNER]

    @property
    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == (
                f"/public/markets/{self.market_id}"
            ):
                self.gets += 1
                if self.on_get is not None:
                    await self.on_get(self.gets)
                if self.get_raises is not None:
                    raise self.get_raises
                return httpx.Response(self.get_status_code, json=self.body)

            if request.method == "POST" and request.url.path == (
                f"/markets/{self.market_id}/settle"
            ):
                self.posts += 1
                self.post_tokens.append(
                    request.headers.get("Authorization", "").removeprefix("Bearer ")
                )
                if self.on_post is not None:
                    await self.on_post(self.posts)
                if self.post_raises is not None:
                    raise self.post_raises
                return httpx.Response(self.post_status_code, json={})

            raise AssertionError(f"unexpected call {request.method} {request.url}")

        return httpx.MockTransport(handler)

    @property
    def gate_open(self) -> httpx.MockTransport:
        """The same market, answering `open`: the trade's gate stubbed open, so
        only the latch and the book lock can refuse a trade (#12's test notes).
        Counts nothing; a test asserts on the settlement's calls only."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={**self.body, "status": "open"})

        return httpx.MockTransport(handler)


def admin_token(actor) -> str:  # noqa: ANN001
    return mint_token(actor.id, UserRole.ADMIN, username=actor.username)


async def settle(
    session: AsyncSession,
    upstream: SettleUpstream,
    *,
    actor=None,  # noqa: ANN001
    transport: httpx.AsyncBaseTransport | None = None,
    **extra: Any,
):
    """One settlement, with the interface these tests assume.

    **The signature is an assumption, and this is the single place it is
    written down**: session and market id positionally, the rest keyword-only,
    the administrator's `Actor` and their own token forwarded, the terms client
    injected, as `trading.execute` takes them. `now` is passed only by the
    tests that pin it ("Tests may pass `now` in").
    """
    actor = actor if actor is not None else admin()
    return await settlement().pay_out(
        session,
        upstream.market_id,
        actor=actor,
        access_token=admin_token(actor),
        terms_client=terms_client_over(
            upstream.transport if transport is None else transport
        ),
        **extra,
    )


# --- the book and its holders ---------------------------------------------
async def warm(session: AsyncSession, upstream: SettleUpstream):
    """A book opened and funded with `SUBSIDY`, committed. The call counts are
    reset, so a test counts only its own calls."""
    book = await books().ensure_open(
        session,
        upstream.market_id,
        access_token=mint_token(uuid.uuid4()),
        terms_client=terms_client_over(upstream.transport),
    )
    upstream.gets = 0
    upstream.posts = 0
    return book


def basis_of(quantity: Decimal) -> Decimal:
    """Some cost basis for a seeded position. Settlement never reads it."""
    return (quantity * Decimal("0.6137")).quantize(QUANTUM, rounding=ROUND_HALF_UP)


async def hold(
    session: AsyncSession,
    upstream: SettleUpstream,
    holdings: Sequence[tuple[uuid.UUID, int, Decimal]],
) -> None:
    """Positions as trading would have left them, inserted in the order given,
    with real USER accounts and `q` set to the sum of the positions, so
    "shares outstanding equal the sum of positions" holds. Committed.

    `(user_id, outcome position, quantity)`; a quantity of zero is a holder who
    sold out.
    """
    ents = entities()
    now = datetime.now(UTC)
    q = [ZERO] * len(upstream.outcomes)
    for user_id, position, quantity in holdings:
        await accounts_module().ensure(session, ents.AccountKind.USER, user_id)
        session.add(
            ents.Position(
                user_id=user_id,
                market_id=upstream.market_id,
                outcome_id=upstream.outcomes[position],
                quantity=quantity,
                cost_basis=basis_of(quantity),
                created_at=now,
                updated_at=now,
            )
        )
        q[position] += quantity
    await session.flush()
    await set_q(session, upstream.market_id, q)


async def settled_market(
    session: AsyncSession, holdings: Sequence[tuple[uuid.UUID, int, Decimal]]
) -> SettleUpstream:
    """A warm market holding `holdings` (as `hold` takes them), settled for
    outcome `WINNER` through the real `pay_out`. Committed; the call counts
    are reset, so a test counts only its own calls."""
    upstream = SettleUpstream()
    await warm(session, upstream)
    await hold(session, upstream, holdings)
    await settle(session, upstream)
    upstream.gets = 0
    upstream.posts = 0
    return upstream


# --- reading back -----------------------------------------------------------
def payout_key(market_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"settlement:{market_id}:{user_id}"


def residue_key(market_id: uuid.UUID) -> str:
    return f"settlement_residue:{market_id}"


async def transaction_by_key(session: AsyncSession, key: str):
    transaction = entities().Transaction
    session.expire_all()
    return (
        await session.execute(
            select(transaction).where(transaction.idempotency_key == key)
        )
    ).scalar_one_or_none()


async def legs_of(session: AsyncSession, transaction_id: uuid.UUID) -> dict:
    """`{account_id: amount}` for one transaction; asserts no account appears
    twice, which is "a transaction puts at most one leg on any account"."""
    entry = entities().Entry
    rows = (
        await session.execute(
            select(entry.account_id, entry.amount).where(
                entry.transaction_id == transaction_id
            )
        )
    ).all()
    legs = {account_id: amount for account_id, amount in rows}
    assert len(legs) == len(rows), "one account carries two legs of one transaction"
    return legs


async def account_id_of(session: AsyncSession, kind, owner_id: uuid.UUID):  # noqa: ANN001
    account = await accounts_module().find(session, kind, owner_id)
    assert account is not None, f"no {kind} account for {owner_id}"
    return account.id


async def platform_id(session: AsyncSession) -> uuid.UUID:
    ents = entities()
    return await account_id_of(
        session, ents.AccountKind.PLATFORM, ents.PLATFORM_OWNER_ID
    )


async def balance_of_account(session: AsyncSession, account_id: uuid.UUID) -> Decimal:
    session.expire_all()
    return await accounts_module().balance_of(session, account_id)


async def result_count(session: AsyncSession, market_id: uuid.UUID) -> int:
    result = entities().MarketResult
    return (
        await session.execute(
            select(func.count())
            .select_from(result)
            .where(result.market_id == market_id)
        )
    ).scalar_one()


async def ledger_counts(session: AsyncSession) -> tuple[int, int]:
    """Transactions and entries in the whole ledger, as this session sees them."""
    ents = entities()
    session.expire_all()
    transactions = (
        await session.execute(select(func.count()).select_from(ents.Transaction))
    ).scalar_one()
    entries = (
        await session.execute(select(func.count()).select_from(ents.Entry))
    ).scalar_one()
    return transactions, entries


async def committed_counts(market_id: uuid.UUID) -> tuple[int, int, int]:
    """Transactions, entries and result rows, from a session of its own: only
    what has been committed."""
    async with session_factory()() as other:
        transactions, entries = await ledger_counts(other)
        return transactions, entries, await result_count(other, market_id)


async def positions_and_q(session: AsyncSession, market_id: uuid.UUID) -> tuple:
    """Every position and every `q` in this market, as they stand."""
    ents = entities()
    session.expire_all()
    positions = (
        await session.execute(
            select(
                ents.Position.user_id,
                ents.Position.outcome_id,
                ents.Position.quantity,
                ents.Position.cost_basis,
                ents.Position.updated_at,
            )
            .where(ents.Position.market_id == market_id)
            .order_by(ents.Position.user_id, ents.Position.outcome_id)
        )
    ).all()
    q = (
        await session.execute(
            select(ents.MarketOutcome.q)
            .where(ents.MarketOutcome.market_id == market_id)
            .order_by(ents.MarketOutcome.position)
        )
    ).scalars()
    return list(positions), list(q)


async def settled_entries(reader: AsyncSession, *actor_ids: uuid.UUID) -> list:
    """`market.settled` entries by these actors, read as `audit_svc`."""
    from shared.audit import admin_actions  # noqa: PLC0415

    rows = (
        await reader.execute(
            select(admin_actions).where(
                admin_actions.c.action_type == "market.settled",
                admin_actions.c.actor_id.in_(actor_ids),
            )
        )
    ).mappings().all()
    await reader.rollback()
    return [dict(row) for row in rows]


async def book_lock_is_free(market_id: uuid.UUID) -> bool:
    """Whether another session can take the book row lock right now."""
    from sqlalchemy.exc import DBAPIError  # noqa: PLC0415

    book = entities().MarketBook
    async with session_factory()() as other:
        try:
            await other.execute(
                select(book.market_id)
                .where(book.market_id == market_id)
                .with_for_update(nowait=True)
            )
        except DBAPIError:
            return False
        finally:
            await other.rollback()
    return True


@contextmanager
def capture_sql_with_parameters() -> Iterator[list[tuple[str, tuple]]]:
    """Every statement and its parameters, from the engine."""
    from core.database import get_engine  # noqa: PLC0415

    statements: list[tuple[str, tuple]] = []
    engine = get_engine().sync_engine

    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append((statement, tuple(parameters or ())))

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


def locked_account_ids(statements: Sequence[tuple[str, tuple]]) -> set:
    return {
        parameter
        for statement, parameters in statements
        if "for update" in statement.lower() and "accounts" in statement.lower()
        for parameter in parameters
    }


@contextmanager
def count_commits() -> Iterator[list[int]]:
    """Root COMMITs on the engine's connections: `test_post_all.py`'s shape.
    Not the session's `after_commit`, which a released SAVEPOINT fires too."""
    from core.database import get_engine  # noqa: PLC0415

    commits = [0]
    engine = get_engine().sync_engine

    def count(_conn) -> None:  # noqa: ANN001
        commits[0] += 1

    event.listen(engine, "commit", count)
    try:
        yield commits
    finally:
        event.remove(engine, "commit", count)


# --- pausing one session, watching another queue --------------------------
class Pause:
    """Wraps `module.name` so that one session's `call`-th call pauses after
    the real call returns, until `release`. Every session's results are kept.

    Wraps at the module attribute, which is how the service calls across
    modules (`books.lock_book`), so it sees the call wherever it comes from.
    `monkeypatch.setattr` raises if the name does not exist.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        module: Any,
        name: str,
        *,
        session: AsyncSession,
        call: int,
    ) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.results: dict[int, list[Any]] = {}
        real = getattr(module, name)

        async def wrapper(caller_session, *args, **kwargs):  # noqa: ANN001, ANN202
            result = await real(caller_session, *args, **kwargs)
            seen = self.results.setdefault(id(caller_session), [])
            seen.append(result)
            if caller_session is session and len(seen) == call:
                self.reached.set()
                await self.release.wait()
            return result

        monkeypatch.setattr(module, name, wrapper)

    def results_for(self, session: AsyncSession) -> list[Any]:
        return self.results.get(id(session), [])


def _raise_if_failed(tasks: Sequence[asyncio.Task]) -> None:
    """A party that died stops the wait with its own error, not a timeout."""
    for task in tasks:
        if task.done() and not task.cancelled() and task.exception() is not None:
            raise task.exception()


async def reached(
    flag: asyncio.Event, what: str, tasks: Sequence[asyncio.Task] = ()
) -> None:
    """Wait for `flag`, or fail naming `what`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROBE_TIMEOUT
    while not flag.is_set():
        _raise_if_failed(tasks)
        if loop.time() > deadline:
            pytest.fail(f"timed out: {what}")
        await asyncio.sleep(0.01)


_BOOK_LOCK_WAITERS = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() "
    "AND usename = current_user "
    "AND wait_event_type = 'Lock' "
    "AND query ILIKE '%market_books%' "
    "AND query ILIKE '%for update%' "
    "AND pid <> pg_backend_pid()"
)


async def book_lock_waiters(
    count: int, what: str, tasks: Sequence[asyncio.Task] = ()
) -> None:
    """Wait until exactly `count` backends are queued on a book row lock, or
    fail naming `what`.

    By query text, not by pid as auth's `_wait_until_blocked_on_a_lock` does:
    the trade and settlement paths roll back before their HTTP calls, so a
    party's backend can change mid-request. A fresh connection per poll,
    because `pg_stat_activity` is snapshotted once per transaction.
    """
    from core.database import get_engine  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    deadline = loop.time() + PROBE_TIMEOUT
    while True:
        _raise_if_failed(tasks)
        async with get_engine().connect() as conn:
            waiting = (await conn.execute(_BOOK_LOCK_WAITERS)).scalar_one()
        if waiting == count:
            return
        if loop.time() > deadline:
            pytest.fail(f"timed out: {what} (book-lock waiters: {waiting})")
        await asyncio.sleep(0.01)


async def finish(tasks: Sequence[asyncio.Task], *flags: asyncio.Event) -> list:
    """Release every pause and collect every party, never hanging the suite."""
    for flag in flags:
        flag.set()
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            return await asyncio.gather(*tasks, return_exceptions=True)
    except TimeoutError:
        for task in tasks:
            task.cancel()
        pytest.fail("timed out: a party never finished after every pause was released")


def raise_any(outcomes: Sequence[Any]) -> None:
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
