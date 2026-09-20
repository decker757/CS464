"""Opening a market's book, and funding its pool. [F-7] #96, D-008, D-009.

The ledger has never heard of a market. ADR 0005 has `b` and the subsidy
crossing at publish as an immutable snapshot, and that handoff was never built —
`publish` writes an audit entry and nothing else. D-008 resolves it as a lazy
pull on first touch, which is the shape the starting grant already uses: no
event, no outbox, and no window in which the state is observably wrong, because
the read that would observe the window is the read that closes it.

Business rules, driven through the service layer with no HTTP *inbound*. There
is no route to this and there will not be one in this ticket — the callers are
[T-1] #21's preview and [T-2] #22's trade, and neither exists yet. What lands
here is the primitive, exactly as `service/posting.py` landed before its caller.

The *outbound* call is mocked at the transport, so these tests exercise the real
request and the real parsing. `test_market_terms.py` owns the wire format;
this file owns what gets written once the terms arrive.

Concurrency lives in `test_book_concurrency.py`, because a race needs two
sessions and two connections and the setup is worth keeping separate from the
single-caller rules below.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import (
    Account,
    AccountKind,
    Entry,
    Transaction,
    TransactionKind,
)
from service import accounts, posting
from unit_test.conftest import mint_token


def _books():
    """Imported inside each test rather than at module scope.

    `service/books.py` does not exist yet, and a top-level import would be one
    collection error taking the whole file down as a single red line. Reached
    through here, every test fails on its own, named after the criterion it
    holds (D-007). Same convention as `market_service`'s `test_browsing.py`.
    """
    from service import books  # noqa: PLC0415

    return books


def _entities():
    """`model/entities.py` exists; `MarketBook` and `MarketOutcome` do not yet."""
    from model import entities  # noqa: PLC0415

    return entities


def _errors():
    """`core/errors.py` exists; the book errors below do not yet."""
    from core import errors  # noqa: PLC0415

    return errors

ZERO = Decimal(0)

_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")


def _market_id() -> uuid.UUID:
    return uuid.uuid4()


def _outcome_ids() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4()


class _Upstream:
    """A stand-in market service that counts what it was asked.

    The count is load-bearing in more than one test below: D-008's "a second
    touch re-funds nothing" is the weaker of two claims, and the stronger one
    — a second touch does not call out at all — is only observable here.
    """

    def __init__(
        self,
        *,
        liquidity_b: Decimal | str = _B,
        seed_subsidy: Decimal | str = _SUBSIDY,
        published_at: str | None = "2026-09-01T09:00:00Z",
        outcomes: list[uuid.UUID] | None = None,
        market_id: uuid.UUID | None = None,
    ) -> None:
        self.calls = 0
        self.market_id = market_id or _market_id()
        self.outcomes = outcomes or list(_outcome_ids())
        self._body = {
            "id": str(self.market_id),
            "status": "open",
            "close_time": "2027-01-05T12:00:00Z",
            "resolution_time": "2027-01-20T12:00:00Z",
            "liquidity_b": None if liquidity_b is None else str(liquidity_b),
            "seed_subsidy": None if seed_subsidy is None else str(seed_subsidy),
            "published_at": published_at,
            "outcomes": [
                {"id": str(oid), "position": i, "label": f"Outcome {i}"}
                for i, oid in enumerate(self.outcomes)
            ],
        }

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return httpx.Response(200, json=self._body)

        return httpx.MockTransport(handler)


def _token() -> str:
    return mint_token(uuid.uuid4())


async def _open(session: AsyncSession, upstream: _Upstream, **kwargs):
    return await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=_token(),
        transport=upstream.transport,
        **kwargs,
    )


async def _book(session: AsyncSession, market_id: uuid.UUID):
    stmt = select(_entities().MarketBook).where(_entities().MarketBook.market_id == market_id)
    return (await session.execute(stmt)).scalar_one_or_none()


# --- the first touch ------------------------------------------------------
async def test_the_first_touch_creates_the_book_from_the_market_s_terms(
    session: AsyncSession,
) -> None:
    """D-008's first acceptance criterion, end to end.

    Nothing existed; one call later there is a book carrying the snapshot. The
    terms are a *copy* and that is the point — ADR 0005 calls it duplication
    without coupling, because [1.4] #4 forbids editing a published market's
    terms, so a copy of data that cannot change cannot drift from its source.
    """
    upstream = _Upstream()

    book = await _open(session, upstream)

    assert book.market_id == upstream.market_id
    assert book.liquidity_b == _B
    assert book.seed_subsidy == _SUBSIDY
    assert upstream.calls == 1

    stored = await _book(session, upstream.market_id)
    assert stored is not None


async def test_the_terms_are_stored_as_exact_decimals(session: AsyncSession) -> None:
    """D-016, carried all the way to the column rather than only to the parse.

    `test_market_terms.py` proves the value survives the wire. This proves it
    survives the write, which is a different failure: a `Numeric(18, 4)` column
    and a value that went through a float disagree in the fourth decimal place,
    and nothing reports it.

    Eighteen significant digits, for the reason spelled out there — a friendly
    value passes this assertion even when routed through an IEEE double, so it
    would not be a test of anything.
    """
    exact = Decimal("12345678901234.5678")
    upstream = _Upstream(liquidity_b=exact, seed_subsidy=exact)

    await _open(session, upstream)

    session.expire_all()
    stored = await _book(session, upstream.market_id)

    assert stored is not None
    assert stored.liquidity_b == exact
    assert stored.seed_subsidy == exact


async def test_one_outcome_row_is_written_per_outcome_at_q_zero(
    session: AsyncSession,
) -> None:
    """`ledger.market_outcomes`, and the state a market opens in.

    `q` is zero for every outcome, which is what makes the opening price
    uniform — `C(q)` at `q = 0` gives every outcome `1/n`. No label: that is
    display prose the market service owns, and copying it would be a second
    source of truth for a string this side never renders.
    """
    upstream = _Upstream()

    await _open(session, upstream)

    rows = list(
        (
            await session.execute(
                select(_entities().MarketOutcome)
                .where(_entities().MarketOutcome.market_id == upstream.market_id)
                .order_by(_entities().MarketOutcome.position)
            )
        ).scalars()
    )

    assert [r.outcome_id for r in rows] == upstream.outcomes
    assert [r.position for r in rows] == [0, 1]
    assert all(r.q == ZERO for r in rows)


async def test_the_book_opens_at_state_version_zero(session: AsyncSession) -> None:
    """D-011: `state_version` is the quote reference, and it starts here.

    The ticket's "the first trade publishes 1" belongs to [T-2] #22, which owns
    the only code that increments this. There is no trade path in this ticket
    and a test that faked one would be asserting against its own stub.
    """
    book = await _open(session, _Upstream())

    assert book.state_version == 0


async def test_state_changed_at_equals_opened_at_on_a_never_traded_market(
    session: AsyncSession,
) -> None:
    """D-027, and equality rather than merely non-null.

    For a market nobody has traded, the book's creation *is* its last state
    change — `state_version` is 0 and every `q` is 0, and that state began when
    this row was written. Any other value invents a moment that did not happen.

    This is also what the realtime snapshot reports as `occurred_at` for such a
    market, which is the open question D-027 closed: the contract defines
    `occurred_at` only as "the time of the event", and a market with no events
    has none to name.
    """
    book = await _open(session, _Upstream())

    assert book.state_changed_at == book.opened_at
    assert book.opened_at is not None


# --- D-009: the seed subsidy --------------------------------------------
async def test_the_subsidy_is_posted_from_the_platform_to_the_pool(
    session: AsyncSession,
) -> None:
    """D-009. Two legs, summing to zero, exactly like the signup grant.

    The platform goes down and the pool goes up, so the subsidy is a *movement*
    rather than credits appearing from nowhere. That is what keeps
    `SUM(amount)` over the whole of `ledger.entries` at zero, which is the one
    assertion that would catch almost any mistake in this path.
    """
    upstream = _Upstream()

    book = await _open(session, upstream)

    platform = await accounts.ensure_platform(session)
    pool_balance = await accounts.balance_of(session, book.pool_account_id)

    assert pool_balance == _SUBSIDY
    assert await accounts.balance_of(session, platform.id) == -_SUBSIDY


async def test_the_pool_account_is_keyed_on_the_market_id(
    session: AsyncSession,
) -> None:
    """D-026, and the thing the insert race depends on.

    `Account.owner_id` is `mapped_column(Uuid, nullable=False)` and a market id
    is a `Uuid`, so the market goes straight in it and
    `uq_accounts_kind_owner` comes to mean "one pool per market".

    A sentinel or a fresh `uuid4` here would type-check and pass every test in
    this file. It would fail only under concurrency, by letting two callers
    each create a pool account for one market — which is why this is asserted
    directly rather than left to `test_book_concurrency.py` to catch.
    """
    upstream = _Upstream()

    book = await _open(session, upstream)

    pool = (
        await session.execute(select(Account).where(Account.id == book.pool_account_id))
    ).scalar_one()

    assert pool.kind is AccountKind.MARKET_POOL
    assert pool.owner_id == upstream.market_id

    found = await accounts.find(session, AccountKind.MARKET_POOL, upstream.market_id)
    assert found is not None and found.id == book.pool_account_id


async def test_the_funding_transaction_is_a_market_seed(
    session: AsyncSession,
) -> None:
    """A kind of its own, so the history feed can say what this was.

    `SIGNUP_GRANT` would render a market's subsidy on somebody's statement as a
    welcome bonus. `TransactionKind` is a non-native `Enum`, so adding a member
    is a Python change and never an `ALTER TYPE` — the same property that lets
    `AccountKind.MARKET_POOL` arrive without a migration.
    """
    upstream = _Upstream()

    await _open(session, upstream)

    transaction = await posting.find_by_idempotency_key(
        session, _books().market_open_key(upstream.market_id)
    )

    assert transaction is not None
    assert transaction.kind is TransactionKind.MARKET_SEED


async def test_an_unfunded_pool_is_allowed_to_go_negative(
    session: AsyncSession,
) -> None:
    """D-009's actual mechanism, asserted rather than assumed.

    `_refuse_overdrafts` skips every account that is not a `USER`, so a pool
    drained past its subsidy goes quietly negative instead of failing. That is
    required, not tolerated: LMSR pays out up to `b·ln(n)` more than it
    collects, and a market maker that refused to pay a winner because its pool
    was empty would be a market maker that does not work.

    Spending straight out of a freshly opened pool is the smallest way to prove
    the exemption is real. If this raises `InsufficientFunds`, every settlement
    in [3.4] #12 fails the moment a market pays out more than it took in.
    """
    upstream = _Upstream()
    book = await _open(session, upstream)

    pool = (
        await session.execute(select(Account).where(Account.id == book.pool_account_id))
    ).scalar_one()
    platform = await accounts.ensure_platform(session)

    await posting.post(
        session,
        idempotency_key=f"drain:{uuid.uuid4()}",
        kind=TransactionKind.MARKET_SEED,
        legs=[
            posting.Leg(account=pool, amount=-(_SUBSIDY * 2)),
            posting.Leg(account=platform, amount=_SUBSIDY * 2),
        ],
    )

    assert await accounts.balance_of(session, pool.id) == -_SUBSIDY


# --- the second touch ----------------------------------------------------
async def test_a_second_touch_returns_the_same_book_and_funds_nothing(
    session: AsyncSession,
) -> None:
    """D-008's "a second touch re-funds nothing".

    The balance is the assertion that matters. A pool funded twice is a market
    with double the subsidy the administrator approved, and the platform
    account carrying a liability nobody agreed to.
    """
    upstream = _Upstream()

    first = await _open(session, upstream)
    second = await _open(session, upstream)

    assert second.market_id == first.market_id
    assert second.pool_account_id == first.pool_account_id
    assert await accounts.balance_of(session, first.pool_account_id) == _SUBSIDY

    funding = (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.kind == TransactionKind.MARKET_SEED)
        )
    ).scalar_one()
    assert funding == 1


async def test_a_second_touch_makes_no_http_call_at_all(
    session: AsyncSession,
) -> None:
    """The stronger claim, and the one that decides the order of operations.

    Read the book first. If it is there, return it — no HTTP, no idempotency
    key lookup, nothing. The key protects the *funding transaction*; it is not
    the fast path, and building the legs before checking for the book would
    make every preview and every trade in an open market pay for a network
    round trip to the market service.

    This is the shape `grants.ensure_granted` already uses one layer down, and
    for a sharper version of the same reason: it asks whether the grant exists
    before it reads `STARTING_CREDITS`, so that changing the setting cannot
    invalidate a fingerprint. Here the inputs come from another service over
    the network, so the same ordering also removes the dependency entirely once
    the book exists.
    """
    upstream = _Upstream()

    await _open(session, upstream)
    assert upstream.calls == 1

    await _open(session, upstream)
    await _open(session, upstream)

    assert upstream.calls == 1, "an existing book must not be re-fetched"


async def test_the_book_survives_a_market_service_that_has_since_gone_down(
    session: AsyncSession,
) -> None:
    """The practical consequence of reading the book first.

    Once a market has a book, trading in it does not depend on the market
    service being reachable at all. If the terms were re-fetched on every touch,
    a market service outage would stop trading in every already-open market —
    turning a dependency that D-008 accepted for *first* touches into one that
    sits in the hot path forever.
    """
    upstream = _Upstream()
    await _open(session, upstream)

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("market service is down")

    book = await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=_token(),
        transport=httpx.MockTransport(dead),
    )

    assert book.liquidity_b == _B


# --- refusals -------------------------------------------------------------
async def test_a_market_that_is_not_published_gets_no_book(
    session: AsyncSession,
) -> None:
    """The ticket's "book creation is refused for a market that isn't published".

    `published_at is not None` is the condition, read off the response rather
    than inferred from the status code. The 200/404 split does imply publication
    today — `browsing.get_published` filters to the four public statuses — but
    the book depends on publication, not on a route's current visibility rule,
    and this is a one-field check that keeps the two from drifting.

    Not the *status*: a CLOSED, PENDING_RESOLUTION or APPROVED market is
    published and does get a book. See the test below.
    """
    upstream = _Upstream(published_at=None)

    with pytest.raises(_errors().MarketNotPublished):
        await _open(session, upstream)


async def test_a_closed_market_still_gets_a_book(session: AsyncSession) -> None:
    """Published is the gate, not tradeable. ADR 0011 stops trading, not reading.

    [3.4] #12's settlement reads `q` from a book whose market stopped trading
    weeks earlier, and the realtime snapshot serves a closed market's prices.
    Gating on `status == "open"` here would make both impossible, and would do
    it silently — the first symptom would be a settlement that cannot find the
    positions it is supposed to pay out.
    """
    upstream = _Upstream()
    upstream._body["status"] = "closed"

    book = await _open(session, upstream)

    assert book.liquidity_b == _B


async def test_a_refused_market_leaves_nothing_behind(
    session: AsyncSession,
) -> None:
    """"No partial book" — the other half of the unreachable-service criterion.

    A book with no funding, a pool account with no book, or a funding
    transaction with no outcomes are each worse than the clean failure, because
    each one makes the *next* call take the fast path and return a book that was
    never finished. Whatever went wrong, the market must be left looking exactly
    as untouched as it was.
    """
    upstream = _Upstream(published_at=None)

    with pytest.raises(_errors().MarketNotPublished):
        await _open(session, upstream)

    await session.rollback()

    assert await _book(session, upstream.market_id) is None
    assert (
        await accounts.find(session, AccountKind.MARKET_POOL, upstream.market_id)
    ) is None
    assert (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one() == 0


async def test_an_unreachable_market_service_leaves_nothing_behind(
    session: AsyncSession,
) -> None:
    """The ticket's tenth criterion, stated as state rather than as an error.

    `test_market_terms.py` owns which exception this is. What this owns is that
    the database is untouched afterwards — no book, no pool account, no entries.
    """
    market_id = _market_id()

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _books().ensure_open(
            session,
            market_id,
            access_token=_token(),
            transport=httpx.MockTransport(dead),
        )

    await session.rollback()

    assert await _book(session, market_id) is None
    assert (await accounts.find(session, AccountKind.MARKET_POOL, market_id)) is None
    assert (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one() == 0


# --- the invariant --------------------------------------------------------
async def test_the_ledger_sums_to_zero_after_funding(session: AsyncSession) -> None:
    """The single strongest assertion available about this service.

    Every entry ever written, summed. If the subsidy was credited without being
    debited, or a leg was dropped, or one side was rounded and the other was
    not, this is not zero.
    """
    for _ in range(3):
        await _open(session, _Upstream())

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()

    assert total == ZERO


async def test_every_funding_transaction_balances_on_its_own(
    session: AsyncSession,
) -> None:
    """The whole ledger summing to zero would also hold if two mistakes cancelled.

    Three markets funded from one platform account is exactly the shape where
    that could happen — an over-credit on one pool and an under-credit on
    another net out across the table and show up only here.
    """
    for _ in range(3):
        await _open(session, _Upstream())

    unbalanced = (
        await session.execute(
            select(Entry.transaction_id)
            .group_by(Entry.transaction_id)
            .having(func.sum(Entry.amount) != ZERO)
        )
    ).scalars()

    assert list(unbalanced) == []


async def test_the_grant_and_the_subsidy_do_not_collide_on_a_key(
    session: AsyncSession,
) -> None:
    """Namespaced keys, and a market id that could also be a user id.

    `idempotency_key` is unique across the whole ledger rather than per account,
    so `market-open:<id>` and `signup-grant:<id>` have to be distinguishable
    even when the two ids are byte-identical. They never are in practice; the
    constraint is what makes that not matter.
    """
    shared_id = uuid.uuid4()
    upstream = _Upstream(market_id=shared_id)

    from service import grants  # noqa: PLC0415

    await grants.ensure_granted(session, shared_id)
    await _open(session, upstream)

    assert grants.grant_key(shared_id) != _books().market_open_key(shared_id)

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO
