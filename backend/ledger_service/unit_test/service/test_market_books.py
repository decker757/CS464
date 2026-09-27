"""Opening a market's book, and funding its pool. [F-7] #96, D-008, D-009.

Driven through the service layer, with the outbound call mocked at the
transport. `test_market_terms.py` owns the wire format and
`test_book_concurrency.py` the races; this file owns what gets written.
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
from core.database import get_engine
from service import accounts, posting
from unit_test.conftest import mint_token


def _books():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import books  # noqa: PLC0415

    return books


def _entities():
    """`model/entities.py`, reached lazily like `_books`."""
    from model import entities  # noqa: PLC0415

    return entities


def _errors():
    """`core/errors.py`, reached lazily like `_books`."""
    from core import errors  # noqa: PLC0415

    return errors

ZERO = Decimal(0)

_B = Decimal("100.0000")
_SUBSIDY = Decimal("250.0000")


def _market_id() -> uuid.UUID:
    return uuid.uuid4()


def _outcome_ids() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4()


def _raw(outcomes: list[object]) -> bool:
    """True when the caller handed whole outcome dicts rather than ids. An
    empty list counts as raw: the "no outcomes at all" case."""
    return outcomes == [] or isinstance(outcomes[0], dict)


class _Upstream:
    """A stand-in market service that counts what it was asked, so "a second
    touch does not call out at all" is observable."""

    def __init__(
        self,
        *,
        liquidity_b: Decimal | str = _B,
        seed_subsidy: Decimal | str = _SUBSIDY,
        published_at: str | None = "2026-09-01T09:00:00Z",
        outcomes: list[uuid.UUID] | list[dict[str, object]] | None = None,
        market_id: uuid.UUID | None = None,
    ) -> None:
        self.calls = 0
        self.market_id = market_id or _market_id()
        self.outcomes = (
            outcomes
            if outcomes and not _raw(outcomes)
            else list(_outcome_ids())
        )
        self._body = {
            "id": str(self.market_id),
            "status": "open",
            "close_time": "2027-01-05T12:00:00Z",
            "resolution_time": "2027-01-20T12:00:00Z",
            "liquidity_b": None if liquidity_b is None else str(liquidity_b),
            "seed_subsidy": None if seed_subsidy is None else str(seed_subsidy),
            "published_at": published_at,
            # Raw dicts pass straight through, for a malformed outcome list.
            # `is not None`, not truthiness: `outcomes=[]` must stay empty.
            "outcomes": (
                outcomes
                if outcomes is not None and _raw(outcomes)
                else [
                    {"id": str(oid), "position": i, "label": f"Outcome {i}"}
                    for i, oid in enumerate(self.outcomes)
                ]
            ),
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
    """D-008's first acceptance criterion, end to end: one call later there is
    a book carrying a copy of the terms (ADR 0005)."""
    upstream = _Upstream()

    book = await _open(session, upstream)

    assert book.market_id == upstream.market_id
    assert book.liquidity_b == _B
    assert book.seed_subsidy == _SUBSIDY
    assert upstream.calls == 1

    stored = await _book(session, upstream.market_id)
    assert stored is not None


async def test_the_terms_are_stored_as_exact_decimals(session: AsyncSession) -> None:
    """D-016, carried to the column: the value survives the write, not only
    the parse. Eighteen digits, because a friendly value survives a float.
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
    """`ledger.market_outcomes`, and the state a market opens in: `q` zero for
    every outcome, so the opening price is uniform."""
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
    """D-011: `state_version` is the quote reference, and it starts at zero."""
    book = await _open(session, _Upstream())

    assert book.state_version == 0


async def test_state_changed_at_equals_opened_at_on_a_never_traded_market(
    session: AsyncSession,
) -> None:
    """D-029, and equality rather than merely non-null."""
    book = await _open(session, _Upstream())

    assert book.state_changed_at == book.opened_at
    assert book.opened_at is not None


# --- D-009: the seed subsidy --------------------------------------------
async def test_the_subsidy_is_posted_from_the_platform_to_the_pool(
    session: AsyncSession,
) -> None:
    """D-009. Two legs, summing to zero, exactly like the signup grant."""
    upstream = _Upstream()

    book = await _open(session, upstream)

    platform = await accounts.ensure_platform(session)
    pool_balance = await accounts.balance_of(session, book.pool_account_id)

    assert pool_balance == _SUBSIDY
    assert await accounts.balance_of(session, platform.id) == -_SUBSIDY


async def test_the_pool_account_is_keyed_on_the_market_id(
    session: AsyncSession,
) -> None:
    """D-028, and the thing the insert race depends on. Asserted directly: a
    fresh `uuid4` owner would fail only under concurrency.
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
    """A kind of its own, so the history feed can say what this was."""
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
    """D-009's mechanism: a pool drained past its subsidy goes negative rather
    than failing, because LMSR can pay out more than it collects.
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
    """D-008's "a second touch re-funds nothing", asserted on the balance."""
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
    """The stronger claim: an existing book is returned before any HTTP or key
    lookup, so a warm market never calls market_service.
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
    """The consequence of reading the book first: an open market does not
    depend on market_service being reachable."""
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

    `published_at` is the condition, not the status code or the status: a
    closed market is published and does get a book (next test).
    """
    upstream = _Upstream(published_at=None)

    with pytest.raises(_errors().MarketNotPublished):
        await _open(session, upstream)


async def test_a_closed_market_still_gets_a_book(session: AsyncSession) -> None:
    """Published is the gate, not tradeable: settlement and the snapshot read
    closed markets' books (ADR 0017)."""
    upstream = _Upstream()
    upstream._body["status"] = "closed"

    book = await _open(session, upstream)

    assert book.liquidity_b == _B


async def test_a_refused_market_leaves_nothing_behind(
    session: AsyncSession,
) -> None:
    """"No partial book": a half-written one would send the next call down the
    fast path with a book that was never finished (D-032)."""
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
    """The ticket's tenth criterion, as state: no book, no pool account, no
    entries."""
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


# --- [F-8] #109: the rules about writing a book (ADR 0017) ----------------
async def test_a_market_with_null_terms_is_unavailable_rather_than_funded(
    session: AsyncSession,
) -> None:
    """A null `b` is refused before the book is built, not by the database,
    whose `IntegrityError` the race handler would mistake for D-010's race.
    Unreachable from a correct market_service, and defended anyway.
    """
    upstream = _Upstream(liquidity_b=None)

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _open(session, upstream)


async def test_a_null_seed_subsidy_is_refused_at_the_book_too(
    session: AsyncSession,
) -> None:
    """The money half: a null subsidy would hand `posting.post` a leg of
    `None`."""
    upstream = _Upstream(seed_subsidy=None)

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _open(session, upstream)


@pytest.mark.parametrize(
    ("label", "outcomes"),
    [
        ("no outcomes at all", []),
        ("a single outcome", [{"id": str(uuid.uuid4()), "position": 0}]),
        (
            "the same outcome id twice",
            (lambda o: [{"id": str(o), "position": 0}, {"id": str(o), "position": 1}])(
                uuid.uuid4()
            ),
        ),
        (
            "two outcomes claiming one position",
            [
                {"id": str(uuid.uuid4()), "position": 0},
                {"id": str(uuid.uuid4()), "position": 0},
            ],
        ),
    ],
)
async def test_terms_that_could_never_be_priced_are_refused(
    session: AsyncSession, label: str, outcomes: list[dict[str, object]]
) -> None:
    """The same defence as a null `liquidity_b`, for the outcome list: the
    book is immutable (ADR 0005), so refuse rather than store.
    """
    upstream = _Upstream(outcomes=outcomes)

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _open(session, upstream)


async def test_a_ten_outcome_market_is_not_refused(session: AsyncSession) -> None:
    """The ceiling is market_service's, and a copy here could refuse a
    published market's book. The floor is about what can be priced at all.
    """
    upstream = _Upstream(outcomes=[uuid.uuid4() for _ in range(10)])

    await _open(session, upstream)

    rows = list(
        (
            await session.execute(
                select(_entities().MarketOutcome).where(
                    _entities().MarketOutcome.market_id == upstream.market_id
                )
            )
        ).scalars()
    )

    assert len(rows) == 10


# --- the invariant --------------------------------------------------------
async def test_the_ledger_sums_to_zero_after_funding(session: AsyncSession) -> None:
    """Every entry ever written, summed, after funding: the invariant."""
    for _ in range(3):
        await _open(session, _Upstream())

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()

    assert total == ZERO


async def test_every_funding_transaction_balances_on_its_own(
    session: AsyncSession,
) -> None:
    """The whole ledger summing to zero would also hold if two mistakes
    cancelled across three pools."""
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
    """Namespaced keys: `idempotency_key` is unique ledger-wide, so the two
    must differ even for one id."""
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


# =========================================================================
# The cold path's connection, and terms that must not reach a book
# =========================================================================
async def test_no_connection_is_held_while_the_terms_are_fetched(
    session: AsyncSession,
) -> None:
    """The cold path must not hold a pooled connection across the HTTP call
    (D-043).

    Asserted from inside the transport, the only code that runs during the
    call, from both the session and the pool. Fails with `ensure_open`'s
    rollback removed.
    """
    observed: dict[str, object] = {}
    upstream = _Upstream()

    def handler(request: httpx.Request) -> httpx.Response:
        observed["in_transaction"] = session.in_transaction()
        observed["checked_out"] = get_engine().pool.checkedout()
        return httpx.Response(200, json=upstream._body)

    await _books().ensure_open(
        session,
        upstream.market_id,
        access_token=_token(),
        transport=httpx.MockTransport(handler),
    )

    assert observed["in_transaction"] is False, (
        "the session still held a transaction while market_service was being "
        "called, so its connection was checked out for the whole round trip"
    )
    assert observed["checked_out"] == 0, (
        f"{observed['checked_out']} connection(s) were checked out of the pool "
        "during the terms fetch"
    )


async def test_a_zero_seed_subsidy_is_unavailable_rather_than_unbalanced(
    session: AsyncSession,
) -> None:
    """A bad subsidy is the upstream's fault: a 503, not a 422 from
    `posting.post`'s zero legs. A negative one would fund the pool backwards
    with nothing downstream objecting. Infinity and NaN slip a bare `<= 0`.
    """
    for bad in ("0", "0.0000", "-250.0000", "Infinity", "NaN"):
        upstream = _Upstream(seed_subsidy=bad)

        with pytest.raises(_errors().MarketTermsUnavailable):
            await _open(session, upstream)

        assert await _books().find(session, upstream.market_id) is None, (
            f"a subsidy of {bad} left a book behind"
        )


async def test_a_non_positive_liquidity_b_is_refused_before_the_book_is_written(
    session: AsyncSession,
) -> None:
    """`b <= 0`, not just a null `b`: the book is immutable, so a bad `b` is
    every price's unmapped 500, forever. Infinity and NaN need `is_finite()`.
    """
    for bad in ("0.0000", "-1.0000", "Infinity", "-Infinity", "NaN"):
        upstream = _Upstream(liquidity_b=bad)

        with pytest.raises(_errors().MarketTermsUnavailable):
            await _open(session, upstream)

        assert await _books().find(session, upstream.market_id) is None, (
            f"a b of {bad} left a book behind"
        )
