"""Fixtures the [T-2] #22 test files share.

This suite's convention is a private `_Upstream` per test file, and five files
land with this one ticket. Copying sixty lines of stub market service five
times would mean five places for the terms body to drift from
`service/market_terms.py::_parse`, and the drift would show up as four green
files and one red one. So the pieces every trade test needs live here, built
from the ones already in `test_preview.py`, `test_market_status.py` and
`test_price_publish.py` rather than invented: `Upstream` is
`test_market_status.py`'s mutable one, because the trade path needs a market
that closes *between* the book's creation and the request; `Recorder` is
`test_price_publish.py`'s, because the publish seam is the same one.

Nothing here imports a project module at module scope. `unit_test/conftest.py`
has to run `load_repo_env()` before `core.config.get_settings` is first
called, and every name below is reached through a function for the same reason
the test files reach `service.trading` that way — nothing in [T-2] #22 exists
yet, and a module-scope import would take five files down as one collection
error instead of failing each test on the criterion it holds (D-007).

**The numbers are not friendly, on purpose.** `B` and the quantities below are
chosen so that no cost this suite computes terminates at four decimal places,
which is what makes "a buy rounds ceiling" an assertion rather than a
tautology. `assert_rounding_is_load_bearing` proves that property of the
constants themselves, so a later edit that quietly picks a round number fails
there instead of silently hollowing out every rounding test.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import mint_token

ZERO = Decimal(0)
QUANTUM = Decimal("0.0001")


# --- lazy module handles --------------------------------------------------
def trading():
    """`service/trading.py`, [T-2] #22's own module. Does not exist yet."""
    from service import trading  # noqa: PLC0415

    return trading


def pricing():
    from core import pricing  # noqa: PLC0415

    return pricing


def books():
    from service import books  # noqa: PLC0415

    return books


def grants():
    from service import grants  # noqa: PLC0415

    return grants


def posting():
    from service import posting  # noqa: PLC0415

    return posting


def accounts_module():
    """Named with a suffix because `accounts` is what callers want to call
    their local variable, and shadowing the helper inside a test is the kind
    of confusion that makes a failure read as a missing attribute."""
    from service import accounts  # noqa: PLC0415

    return accounts


def preview():
    from service import preview  # noqa: PLC0415

    return preview


def entities():
    """`model/entities.py` exists; `Position` and `TRADE_BUY` do not yet."""
    from model import entities  # noqa: PLC0415

    return entities


def errors():
    """`core/errors.py` exists; `QuoteStale` and `PendingWritesOnReplay` do not."""
    from core import errors  # noqa: PLC0415

    return errors


def lmsr():
    from core import lmsr  # noqa: PLC0415

    return lmsr


def session_factory():
    from core.database import get_session_factory  # noqa: PLC0415

    return get_session_factory()


# --- the market ----------------------------------------------------------
#
# `b` is 137 rather than the suite's usual 100 for one reason: with a round
# `b`, several of the quantities below price to a cost that terminates at
# scale 4, and a rounding test against an exact value passes whichever
# direction the implementation rounds.
B = Decimal("137.0000")
SUBSIDY = Decimal("250.0000")

# Asymmetric, so a bug that read the `q` vector by insertion order or by
# outcome id rather than by `position` cannot hide behind two equal prices.
# Non-zero, so every cost below is a difference of two costs rather than a
# cost from the origin, which is the arithmetic a trade actually performs.
Q = [Decimal("311.7000"), Decimal("88.3000")]

# Verified against `core/lmsr.py`: the raw cost of each of these against `Q`
# is non-terminating well past the fifth decimal place, so ROUND_CEILING and
# ROUND_FLOOR give different answers and a buy that floored would be caught.
QUANTITY = Decimal("41.0000")
SMALL_QUANTITY = Decimal("13.3333")

FUTURE = "2027-01-05T12:00:00Z"

# A ceiling, not a duration. Everything here should finish in milliseconds;
# this exists so a lock taken in the wrong place shows up as a named failure
# rather than as a suite that hangs until CI gives up.
TIMEOUT = 30


class Upstream:
    """A stand-in market service whose answer can change between calls.

    `test_market_status.py`'s, because the trade path needs exactly what that
    file needed: a market whose book was opened and funded weeks ago and whose
    status changes afterwards. A fixture that could only answer one way cannot
    express an early close, and an early close is half of what ADR 0017's gate
    is for.

    The call count and the captured tokens are load-bearing below: one hop per
    trade that is not a replay, carrying the caller's own credential and
    nothing minted here.
    """

    def __init__(
        self,
        *,
        status: str = "open",
        published_at: str | None = "2026-09-01T09:00:00Z",
        outcomes: int = 2,
        status_code: int = 200,
    ) -> None:
        self.calls = 0
        self.tokens: list[str] = []
        self.status_code = status_code
        self.raises: Exception | None = None
        self.before_response = None
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]
        self.body: dict[str, object] = {
            "id": str(self.market_id),
            "status": status,
            "close_time": FUTURE,
            "resolution_time": "2027-01-20T12:00:00Z",
            "liquidity_b": str(B),
            "seed_subsidy": str(SUBSIDY),
            "published_at": published_at,
            "outcomes": [
                {"id": str(o), "position": i, "label": f"Outcome {i}"}
                for i, o in enumerate(self.outcomes)
            ],
        }

    def closes(self, *, status: str = "closed") -> None:
        """What the market service starts saying once the market stops.

        `close_time` is left in the future, which is ADR 0014's early close
        exactly: the status moves, `closed_at` is written, and the published
        closing time stays where traders read it.
        """
        self.body["status"] = status

    @property
    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            self.tokens.append(
                request.headers.get("Authorization", "").removeprefix("Bearer ")
            )
            if self.before_response is not None:
                # The seam the "book row is not held across the hop" test
                # needs: somewhere to await while the call is in flight.
                await self.before_response()
            if self.raises is not None:
                raise self.raises
            return httpx.Response(self.status_code, json=self.body)

        return httpx.MockTransport(handler)

    @property
    def dead(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            raise httpx.ConnectError("market service is down")

        return httpx.MockTransport(handler)


class Recorder:
    """A Redis client that remembers, and optionally throws.

    `test_price_publish.py`'s. `publish` takes its client as a parameter, so
    duck typing is enough: there is no `fakeredis` to add and no server to
    start, and the recorder captures the exact channel and the exact JSON,
    which a live round trip would not.

    `on_publish` is the seam the "after the commit" test needs. It runs while
    the publish is in flight, from a session of its own, and can therefore ask
    the database whether the trade is visible yet.
    """

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.on_publish = None
        self._raises = raises

    async def publish(self, channel: str, payload: object) -> int:
        self.calls.append((channel, payload))
        if self.on_publish is not None:
            await self.on_publish()
        if self._raises is not None:
            raise self._raises
        return 0


def token() -> str:
    return mint_token(uuid.uuid4())


# --- driving the thing under test ----------------------------------------
async def buy(
    session: AsyncSession,
    upstream: Upstream,
    *,
    user_id: uuid.UUID,
    outcome: int = 0,
    quantity: Decimal = QUANTITY,
    state_version: int = 0,
    client_key: str = "client-key-1",
    side: str = "buy",
    access_token: str | None = None,
    redis_client: Recorder | None = None,
    transport: httpx.MockTransport | None = None,
):
    """One trade, with the interface these tests assume.

    **The signature is an assumption, and this is the single place it is
    written down.** The issue names the route's five body fields and says the
    ledger builds the movement "from `q` and `b` under the book lock and from
    the token's `sub`"; it does not name the service function. Everything
    below follows the shape `service/preview.py::quote` already has — session
    and market id positionally, everything else keyword-only, `access_token`
    forwarded rather than minted, `transport` and `redis_client` the two
    injectable seams. If the implementation lands on other names, this helper
    is the one place to change.

    `side` arrives as the wire string and is converted at the boundary,
    because that is what the route does: parsed once, passed down as the enum.
    """
    return await trading().execute(
        session,
        upstream.market_id,
        user_id=user_id,
        outcome_id=upstream.outcomes[outcome],
        side=pricing().Side(side),
        quantity=quantity,
        state_version=state_version,
        idempotency_key=client_key,
        access_token=access_token if access_token is not None else token(),
        redis_client=redis_client if redis_client is not None else Recorder(),
        transport=upstream.transport if transport is None else transport,
    )


async def warm(
    session: AsyncSession, upstream: Upstream, q: Sequence[Decimal] = tuple(Q)
):
    """A market whose book exists, is funded, and whose outcomes hold shares.

    Committed by the time this returns — `books.ensure_open` ends in
    `posting.post`, which commits — so the racing sessions below are looking
    at the state a market weeks into trading would be in, and see it under
    READ COMMITTED.

    `q` is written directly rather than traded into place. Every test that
    wants a market somebody has already traded in needs a starting `q` that is
    not the origin, and using the trade path to build one would make the
    fixture depend on the thing under test.
    """
    book = await books().ensure_open(
        session,
        upstream.market_id,
        access_token=token(),
        transport=upstream.transport,
    )
    await set_q(session, upstream, q)
    upstream.calls = 0
    upstream.tokens.clear()
    return book


async def set_q(
    session: AsyncSession, upstream: Upstream, q: Sequence[Decimal]
) -> None:
    outcome = entities().MarketOutcome
    for position, value in enumerate(q):
        await session.execute(
            update(outcome)
            .where(
                outcome.market_id == upstream.market_id,
                outcome.position == position,
            )
            .values(q=value)
        )
    await session.commit()


async def fund(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    """Mint this user's starting credits ahead of time, and return the amount.

    The trade path grants lazily, so a test that skips this is also testing
    `grants.ensure_granted`. Tests about the trade call it first; the two that
    are about the grant deliberately do not.
    """
    from core.config import get_settings  # noqa: PLC0415

    await grants().ensure_granted(session, user_id)
    await session.commit()
    return get_settings().starting_credits


async def book_row(session: AsyncSession, market_id: uuid.UUID):
    book = entities().MarketBook
    session.expire_all()
    return (
        await session.execute(select(book).where(book.market_id == market_id))
    ).scalar_one()


async def q_of(session: AsyncSession, market_id: uuid.UUID) -> list[Decimal]:
    outcome = entities().MarketOutcome
    session.expire_all()
    rows = (
        await session.execute(
            select(outcome.q)
            .where(outcome.market_id == market_id)
            .order_by(outcome.position)
        )
    ).scalars()
    return list(rows)


async def position_of(
    session: AsyncSession,
    user_id: uuid.UUID,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
):
    position = entities().Position
    session.expire_all()
    return (
        await session.execute(
            select(position).where(
                position.user_id == user_id,
                position.market_id == market_id,
                position.outcome_id == outcome_id,
            )
        )
    ).scalar_one_or_none()


async def balance_of_user(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    """Expired *before* the account is loaded, not after.

    `expire_all()` after the find marks the freshly loaded object's
    attributes stale, and reading `account.id` then issues a lazy refresh
    outside a greenlet — `MissingGreenlet` under asyncio, which reads like a
    driver problem and is a test-helper ordering bug. The point of the expiry
    is to see another transaction's commits, and doing it first achieves that
    without leaving a live object holding the bag.
    """
    from service import accounts  # noqa: PLC0415

    session.expire_all()
    account = await accounts.find(session, entities().AccountKind.USER, user_id)
    if account is None:
        return ZERO
    return await accounts.balance_of(session, account.id)


async def pool_balance(session: AsyncSession, market_id: uuid.UUID) -> Decimal:
    from service import accounts  # noqa: PLC0415

    session.expire_all()
    account = await accounts.find(
        session, entities().AccountKind.MARKET_POOL, market_id
    )
    assert account is not None, "no pool account; the book was never opened"
    return await accounts.balance_of(session, account.id)


async def entry_count(session: AsyncSession) -> int:
    session.expire_all()
    return (
        await session.execute(select(func.count()).select_from(entities().Entry))
    ).scalar_one()


async def transaction_count(session: AsyncSession, *, key: str | None = None) -> int:
    transaction = entities().Transaction
    session.expire_all()
    stmt = select(func.count()).select_from(transaction)
    if key is not None:
        stmt = stmt.where(transaction.idempotency_key == key)
    return (await session.execute(stmt)).scalar_one()


# --- the invariants ------------------------------------------------------
async def assert_ledger_balances(session: AsyncSession) -> None:
    """Both halves, because one of them is not enough.

    `SUM(amount)` over the whole table is the single strongest assertion
    available about this service, and it would also be satisfied by two
    mistakes that cancelled — a leg dropped from one transaction and an extra
    leg on another. The per-transaction check is what separates those, and
    `test_concurrency.py` already makes both for the same reason.
    """
    entry = entities().Entry
    session.expire_all()

    total = (
        await session.execute(select(func.coalesce(func.sum(entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO, f"the ledger no longer sums to zero: {total}"

    unbalanced = (
        await session.execute(
            select(entry.transaction_id)
            .group_by(entry.transaction_id)
            .having(func.sum(entry.amount) != ZERO)
        )
    ).scalars()
    assert list(unbalanced) == [], "a transaction's legs do not sum to zero"


async def assert_no_negative_user_balances(session: AsyncSession) -> None:
    """No USER account below zero.

    PLATFORM and MARKET_POOL are exempt by design — the house's balance is
    minus the credits in circulation, and a pool is meant to go negative under
    LMSR — so this asks the question of the one kind that holds money somebody
    could spend twice, which is the shape `posting._refuse_overdrafts` states.
    """
    ents = entities()
    session.expire_all()
    rows = (
        await session.execute(
            select(ents.Account.id, func.coalesce(func.sum(ents.Entry.amount), ZERO))
            .select_from(ents.Account)
            .join(ents.Entry, ents.Entry.account_id == ents.Account.id, isouter=True)
            .where(ents.Account.kind == ents.AccountKind.USER)
            .group_by(ents.Account.id)
        )
    ).all()
    negative = [(str(i), b) for i, b in rows if b < ZERO]
    assert negative == [], f"a user account went negative: {negative}"


# --- the expected arithmetic ---------------------------------------------
#
# Recomputed from `core/lmsr.py` and `core/pricing.py` rather than pinned as
# literals, the same way `test_preview.py` does it. The criterion is that the
# charged number is the number the preview quoted, and both come from those
# two modules — a literal here would be a third restatement of one number, and
# the one most likely to have been copied out of a failing run.
def delta_for(q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal):
    delta = [ZERO] * len(q)
    delta[outcome] = quantity if side == "buy" else -quantity
    return delta


def raw_cost(
    q: Sequence[Decimal],
    outcome: int = 0,
    side: str = "buy",
    quantity: Decimal = QUANTITY,
) -> Decimal:
    return lmsr().cost_to_trade(list(q), B, delta_for(q, outcome, side, quantity))


def expected_total(
    q: Sequence[Decimal],
    outcome: int = 0,
    side: str = "buy",
    quantity: Decimal = QUANTITY,
) -> Decimal:
    """The signed total, built in the order D-039 describes it.

    Magnitude out of the engine, quantized by side — ceiling on a buy — and
    only then the sign applied by the caller: negative on a buy, because
    credits leave the trader. That is the convention `PreviewOut.total`
    already publishes and the one the ledger legs are built from.
    """
    magnitude = pricing().quantize_cost(
        raw_cost(q, outcome, side, quantity).copy_abs(), side=pricing().Side(side)
    )
    return -magnitude if side == "buy" else magnitude


def unaffordable(credits: Decimal) -> Decimal:
    """A buy of outcome 0 against `Q` that costs more than `credits`.

    Derived from the grant rather than written down, so a refusal test still
    refuses for whoever sets `STARTING_CREDITS` in their own `.env`. Outcome 0
    leads `Q`, so a buy of x shares of it never costs less than x - b*ln 2,
    and `credits + 2b` clears any grant. Callers still assert it.
    """
    return credits + 2 * B


def twice_affordable(credits: Decimal) -> Decimal:
    """A buy of outcome 0 against `Q` that `credits` can afford exactly twice.

    For the overdraft races, which need somebody refused whatever the grant
    is. A fixed quantity against a larger grant fills every party: the mixed
    race turns vacuous, and the one-user race asks its barrier for more
    connections than the pool holds and never starts.

    The largest whole-tick buy costing at most `credits / 2.5` — two fit, a
    third does not. Bisected, because the cost has no closed-form inverse.
    """
    budget = credits / Decimal("2.5")
    lo, hi = 0, int(unaffordable(budget) / QUANTUM)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if -expected_total(Q, 0, "buy", mid * QUANTUM) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo * QUANTUM


def floored_total(
    q: Sequence[Decimal], outcome: int = 0, quantity: Decimal = QUANTITY
) -> Decimal:
    """What a buy would charge if it rounded the wrong way.

    The counterfactual the rounding assertions compare against, so "rounds
    ceiling" is a claim about direction rather than about a number that
    happened to match.
    """
    return -raw_cost(q, outcome, "buy", quantity).copy_abs().quantize(
        QUANTUM, rounding=ROUND_FLOOR
    )


def assert_rounding_is_load_bearing(
    q: Sequence[Decimal], outcome: int = 0, quantity: Decimal = QUANTITY
) -> None:
    """A property of the constants, not of the code under test.

    If a cost happens to be exact at scale 4, ceiling and floor agree and
    every rounding assertion in this suite passes whichever way the
    implementation rounds. Asserted rather than assumed, so an edit that picks
    a rounder `b` fails here with a sentence about why instead of hollowing
    out four tests silently.
    """
    magnitude = raw_cost(q, outcome, "buy", quantity).copy_abs()
    up = magnitude.quantize(QUANTUM, rounding=ROUND_CEILING)
    down = magnitude.quantize(QUANTUM, rounding=ROUND_FLOOR)
    assert up != down, (
        f"the cost of {quantity} against q={list(q)} at b={B} is {magnitude}, "
        "which is exact at scale 4 — every rounding assertion built on these "
        "constants is a tautology"
    )


@contextmanager
def capture_sql() -> Iterator[list[str]]:
    """Every statement the block sends to Postgres, in order.

    `test_market_status.py`'s and `test_preview.py`'s helper. Attached to the
    engine rather than inferred from timings, because a structural claim —
    "this read takes no lock", "this path writes nothing" — deserves a
    structural assertion, and `before_cursor_execute` sees the compiled
    statement text without pinning any SQL syntax.
    """
    from sqlalchemy import event  # noqa: PLC0415

    from core.database import get_engine  # noqa: PLC0415

    statements: list[str] = []
    engine = get_engine().sync_engine

    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


def writes(statements: Sequence[str]) -> list[str]:
    return [
        s
        for s in statements
        if s.lstrip().lower().startswith(("insert", "update", "delete"))
    ]


def locks(statements: Sequence[str]) -> list[str]:
    return [s for s in statements if "for update" in s.lower()]
