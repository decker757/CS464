"""The authoritative price read. [F-9] #112, D-050.

Business rules of `service/snapshot.py::snapshot`; status codes and the JSON
shape are `test_snapshot_routes.py`'s. The outbound call is stubbed at the
transport. One joined statement, no lock and no write on the warm path are
asserted from the SQL sent. Above all, it never gates on status (ADR 0017).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import Entry
from unit_test.book_fixtures import (
    Q,
    Upstream,
    book_row,
    books,
    capture_sql,
    entities,
    errors,
    expected_prices,
    fresh_token,
    mentioning,
    warm,
    writes,
)
from unit_test.conftest import mint_token, strip_outcomes


def _snapshot_service():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import snapshot  # noqa: PLC0415

    return snapshot


# --- driving the thing under test ----------------------------------------
async def _read(
    session: AsyncSession,
    upstream: Upstream,
    *,
    access_token: str | None = None,
    transport: httpx.MockTransport | None = None,
):
    """One snapshot, with the caller's own token and an injectable transport."""
    return await _snapshot_service().snapshot(
        session,
        upstream.market_id,
        access_token=access_token if access_token is not None else fresh_token(),
        transport=upstream.transport if transport is None else transport,
    )


# =========================================================================
# What the snapshot returns
# =========================================================================
async def test_the_prices_are_the_lmsr_prices_of_the_books_q_and_b(
    session: AsyncSession,
) -> None:
    """Computed from `core/lmsr.py` over the book's `q`, not cached (ADR 0010)."""
    upstream = Upstream()
    await warm(session, upstream)

    result = await _read(session, upstream)

    assert [p.price for p in result.prices] == expected_prices(Q)


async def test_the_prices_carry_every_outcome_in_position_order(
    session: AsyncSession,
) -> None:
    """Every outcome, ordered by `position` rather than insertion or id."""
    upstream = Upstream(outcomes=3)
    await warm(session, upstream, q=[Decimal("10"), Decimal("40"), Decimal("25")])

    result = await _read(session, upstream)

    assert [p.position for p in result.prices] == [0, 1, 2]
    assert [p.outcome_id for p in result.prices] == upstream.outcomes


async def test_the_prices_are_quantized_to_scale_four(session: AsyncSession) -> None:
    """Scale 4 exactly, so a snapshot and a price frame are the same string."""
    upstream = Upstream()
    await warm(session, upstream)

    result = await _read(session, upstream)

    for entry in result.prices:
        assert entry.price.as_tuple().exponent == -4, entry.price


async def test_the_state_version_is_the_books_own(session: AsyncSession) -> None:
    """D-011: the book's own counter, the one a client resumes from. Written
    directly to stand in for a trade."""
    upstream = Upstream()
    await warm(session, upstream)
    book = entities().MarketBook
    await session.execute(
        update(book).where(book.market_id == upstream.market_id).values(state_version=7)
    )
    await session.commit()

    result = await _read(session, upstream)

    assert result.state_version == 7


# =========================================================================
# occurred_at
# =========================================================================
async def test_occurred_at_is_the_books_state_changed_at(
    session: AsyncSession,
) -> None:
    """Against a book whose state has moved: `opened_at` or `now()` would both
    pass on a never-traded market."""
    upstream = Upstream()
    await warm(session, upstream)
    moved = datetime(2026, 9, 20, 11, 30, tzinfo=UTC)
    book = entities().MarketBook
    await session.execute(
        update(book)
        .where(book.market_id == upstream.market_id)
        .values(state_changed_at=moved, state_version=3)
    )
    await session.commit()

    result = await _read(session, upstream)

    assert result.occurred_at == moved


async def test_a_never_traded_markets_occurred_at_is_its_opened_at(
    session: AsyncSession,
) -> None:
    """D-029, read from the other end: equality, not merely non-null."""
    upstream = Upstream()
    book = await books().ensure_open(
        session,
        upstream.market_id,
        access_token=fresh_token(),
        transport=upstream.transport,
    )
    await session.commit()

    result = await _read(session, upstream)

    assert result.occurred_at == book.opened_at
    assert result.occurred_at == book.state_changed_at


# =========================================================================
# The reads it makes
# =========================================================================
async def test_the_read_is_one_statement_joining_the_two_tables(
    session: AsyncSession,
) -> None:
    """D-013: exactly one statement touches `market_books`, and it joins
    `market_outcomes`, or a client could discard the correcting event as stale.
    """
    upstream = Upstream()
    await warm(session, upstream)

    with capture_sql() as statements:
        await _read(session, upstream)

    books = mentioning(statements, "market_books")
    assert len(books) == 1, f"the warm path must read once, sent:\n{statements}"
    assert "market_outcomes" in books[0].lower(), (
        "`q` and `state_version` must come back from one statement (D-013), "
        f"and this one does not join them:\n{books[0]}"
    )


async def test_a_warm_snapshot_takes_no_locks(session: AsyncSession) -> None:
    """D-012: this read decides no write, so it takes no lock; one would queue
    every viewer behind the trade holding the row. Fails when a lock is added.
    """
    upstream = Upstream()
    await warm(session, upstream)

    with capture_sql() as statements:
        await _read(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking == [], f"the snapshot read must take no locks:\n{locking}"


async def test_a_warm_snapshot_writes_nothing(session: AsyncSession) -> None:
    """A warm snapshot writes nothing: its only permitted write is the first
    touch."""
    upstream = Upstream()
    await warm(session, upstream)

    with capture_sql() as statements:
        await _read(session, upstream)

    assert writes(statements) == [], (
        f"a warm snapshot must not write:\n{writes(statements)}"
    )


async def test_a_warm_snapshot_changes_nothing(session: AsyncSession) -> None:
    """The same claim as state, which fails differently from the capture."""
    upstream = Upstream()
    await warm(session, upstream)
    before = await book_row(session, upstream)
    snapshot = (before.state_version, before.state_changed_at, before.liquidity_b)
    entries_before = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()

    await _read(session, upstream)

    after = await book_row(session, upstream)
    assert (after.state_version, after.state_changed_at, after.liquidity_b) == snapshot
    assert (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one() == entries_before


# =========================================================================
# The cold path — a second first-toucher
# =========================================================================
async def test_a_market_with_no_book_has_one_opened(session: AsyncSession) -> None:
    """D-050: the snapshot is a second first-toucher, and opens the book."""
    upstream = Upstream()

    result = await _read(session, upstream)

    assert upstream.calls == 1
    assert (await book_row(session, upstream)).market_id == upstream.market_id
    assert len(result.prices) == 2


async def test_a_cold_market_prices_at_zero_shares(session: AsyncSession) -> None:
    """A cold two-outcome market opens at an even split."""
    upstream = Upstream()

    result = await _read(session, upstream)

    assert [p.price for p in result.prices] == expected_prices(
        [Decimal(0), Decimal(0)]
    )


async def test_the_callers_own_token_is_forwarded_upstream(
    session: AsyncSession,
) -> None:
    """D-018: the caller's own credential is forwarded, and none is minted."""
    upstream = Upstream()
    token = mint_token(uuid.uuid4())

    await _read(session, upstream, access_token=token)

    assert upstream.tokens == [token]


async def test_a_second_snapshot_makes_no_http_call_at_all(
    session: AsyncSession,
) -> None:
    """The cold path is self-extinguishing: a snapshot fires on every page
    open, so re-reading the terms would load market_service on every reconnect.
    """
    upstream = Upstream()
    await _read(session, upstream)
    upstream.calls = 0

    await _read(session, upstream)

    assert upstream.calls == 0


async def test_the_cold_path_takes_the_handoffs_locks(session: AsyncSession) -> None:
    """D-036: opening a book does take the handoff's locks. Removing them to
    satisfy "no lock" would reopen D-010's race."""
    upstream = Upstream()

    with capture_sql() as statements:
        await _read(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking != [], (
        "the first touch funds a pool and must take the handoff's locks "
        "(D-036); only the pricing read is unlocked"
    )


# =========================================================================
# It never gates on status
# =========================================================================
async def test_a_closed_market_makes_no_status_hop(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is absent, not merely lenient: `ensure_trading` raises if
    called, and no HTTP call is made at all.
    """
    upstream = Upstream(status="closed")
    await warm(session, upstream)

    from service import market_status  # noqa: PLC0415

    async def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "the snapshot must not gate on status (ADR 0017): a closed "
            "market still has a price to render"
        )

    monkeypatch.setattr(market_status, "ensure_trading", forbidden)

    await _read(session, upstream)

    assert upstream.calls == 0, (
        "a warm snapshot made an HTTP call to market_service; ADR 0017's hop "
        "belongs to the trade path, which fires once per trade, not to a read "
        "that fires on every page open and every reconnect"
    )


@pytest.mark.parametrize("status", ["closed", "pending_resolution", "approved"])
async def test_no_status_is_refused(session: AsyncSession, status: str) -> None:
    """No status market_service can report is refused here."""
    upstream = Upstream(status=status)

    result = await _read(session, upstream)

    assert len(result.prices) == 2


# =========================================================================
# Failures, reusing the preview's codes
# =========================================================================
async def test_a_market_that_does_not_exist_raises_market_not_found(
    session: AsyncSession,
) -> None:
    """404 `market_not_found`, from the cold path's terms pull."""
    upstream = Upstream()

    with pytest.raises(errors().MarketNotFound):
        await _read(session, upstream, transport=upstream.missing)


async def test_an_unpublished_market_raises_market_not_published(
    session: AsyncSession,
) -> None:
    """409 `market_not_published`. A market with no `published_at` has no terms
    to open a book from, and nothing to price."""
    upstream = Upstream(published_at=None)

    with pytest.raises(errors().MarketNotPublished):
        await _read(session, upstream)


async def test_an_unreachable_market_service_raises_market_terms_unavailable(
    session: AsyncSession,
) -> None:
    """503 `market_terms_unavailable`, only on a market's first touch."""
    upstream = Upstream()

    with pytest.raises(errors().MarketTermsUnavailable):
        await _read(session, upstream, transport=upstream.dead)


async def test_an_unreachable_market_service_cannot_stop_a_warm_snapshot(
    session: AsyncSession,
) -> None:
    """The fast path holds when market_service is down: a warm price depends on
    Postgres alone."""
    upstream = Upstream()
    await warm(session, upstream)

    result = await _read(session, upstream, transport=upstream.dead)

    assert [p.price for p in result.prices] == expected_prices(Q)


async def test_a_refused_market_leaves_no_book_behind(session: AsyncSession) -> None:
    """Nothing is committed on the way to a refusal (D-032), from this second
    first-toucher."""
    upstream = Upstream(published_at=None)
    book = entities().MarketBook

    with pytest.raises(errors().MarketNotPublished):
        await _read(session, upstream)

    await session.rollback()
    assert (
        await session.execute(
            select(func.count()).select_from(book).where(book.market_id == upstream.market_id)
        )
    ).scalar_one() == 0


# =========================================================================
# One read and one quantizer, shared with the preview (D-052)
# =========================================================================
async def _strip_outcomes(session: AsyncSession, upstream: Upstream) -> None:
    """A book with no outcome rows, as a hand-run repair could leave it."""
    await strip_outcomes(session, upstream.market_id)


async def test_a_book_with_no_outcome_rows_is_a_ledger_error_not_an_index_error(
    session: AsyncSession,
) -> None:
    """A book with no outcome rows reads as no book twice: a named 500, not an
    unmapped `IndexError`."""
    from core.errors import LedgerError  # noqa: PLC0415

    upstream = Upstream()
    await warm(session, upstream)
    await _strip_outcomes(session, upstream)

    with pytest.raises(Exception) as raised:
        await _read(session, upstream)

    assert isinstance(raised.value, LedgerError), (
        f"a book with no outcome rows raised an unmapped "
        f"{type(raised.value).__name__}: {raised.value!r}"
    )
    assert raised.value.status_code == 500
    assert raised.value.code == "market_book_incomplete"


async def test_the_snapshot_and_the_preview_quote_the_same_price_strings(
    session: AsyncSession,
) -> None:
    """Both docs pages promise the preview and the snapshot the same price
    strings for the same state. Compared as strings."""
    from core.pricing import Side  # noqa: PLC0415
    from service import preview  # noqa: PLC0415

    upstream = Upstream()
    await warm(session, upstream)

    snap = await _read(session, upstream)
    quote = await preview.quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[0],
        side=Side.BUY,
        quantity=Decimal("1.0000"),
        access_token=fresh_token(),
        transport=upstream.transport,
    )

    assert [(p.outcome_id, p.position, str(p.price)) for p in snap.prices] == [
        (p.outcome_id, p.position, str(p.price)) for p in quote.prices
    ]
