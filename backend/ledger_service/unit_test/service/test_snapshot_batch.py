"""The batch price read for the browse list. [X-1] #432.

Business rules of `service/snapshot.py::list_snapshots`; status codes, the cap
and the JSON shape are `test_snapshot_batch_routes.py`'s. An id with no book is
absent rather than an error, and no book is opened. One statement reads every
market, with no lock, asserted from the SQL sent.

Expected prices are literals, computed outside the service with `Decimal.exp`
at 40 digits and rounded half up, so they share no code with `core/lmsr.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.book_fixtures import (
    Upstream,
    capture_sql,
    entities,
    mentioning,
    warm,
)
from unit_test.conftest import drop_outcome_row


def _snapshot_service():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import snapshot  # noqa: PLC0415

    return snapshot


async def _move(
    session: AsyncSession,
    upstream: Upstream,
    *,
    state_version: int,
    state_changed_at: datetime,
    credits_traded: Decimal,
) -> None:
    """Stand in for a trade's write to the book's counters and clock."""
    book = entities().MarketBook
    await session.execute(
        update(book)
        .where(book.market_id == upstream.market_id)
        .values(
            state_version=state_version,
            state_changed_at=state_changed_at,
            credits_traded=credits_traded,
        )
    )
    await session.commit()


def _priced(snapshot) -> list[tuple[uuid.UUID, int, Decimal]]:  # noqa: ANN001
    return [(p.outcome_id, p.position, p.price) for p in snapshot.prices]


async def _three_warm_markets(session: AsyncSession) -> list[Upstream]:
    markets = [Upstream(), Upstream(), Upstream()]
    for market in markets:
        await warm(session, market)
    return markets


# =========================================================================
# What the batch returns
# =========================================================================
async def test_each_market_is_answered_with_its_own_book(
    session: AsyncSession,
) -> None:
    """AC1: one call prices many markets, and every field of each snapshot is
    that market's own. Two books in different states, so a row read under the
    wrong market shows."""
    first, second = Upstream(), Upstream(outcomes=3)
    await warm(session, first, q=[Decimal("137.5000"), Decimal("42.2500")])
    await warm(session, second, q=[Decimal("10"), Decimal("40"), Decimal("25")])
    first_moved = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    second_moved = datetime(2026, 10, 2, 14, 5, tzinfo=UTC)
    await _move(
        session,
        first,
        state_version=7,
        state_changed_at=first_moved,
        credits_traded=Decimal("12.3456"),
    )
    await _move(
        session,
        second,
        state_version=3,
        state_changed_at=second_moved,
        credits_traded=Decimal("7.8901"),
    )

    result = await _snapshot_service().list_snapshots(
        session, [first.market_id, second.market_id]
    )

    assert [snapshot.market_id for snapshot in result] == [
        first.market_id,
        second.market_id,
    ]
    first_snapshot, second_snapshot = result

    assert first_snapshot.state_version == 7
    assert first_snapshot.credits_traded == Decimal("12.3456")
    assert first_snapshot.occurred_at == first_moved
    assert _priced(first_snapshot) == [
        (first.outcomes[0], 0, Decimal("0.7216")),
        (first.outcomes[1], 1, Decimal("0.2784")),
    ]

    assert second_snapshot.state_version == 3
    assert second_snapshot.credits_traded == Decimal("7.8901")
    assert second_snapshot.occurred_at == second_moved
    assert _priced(second_snapshot) == [
        (second.outcomes[0], 0, Decimal("0.2848")),
        (second.outcomes[1], 1, Decimal("0.3844")),
        (second.outcomes[2], 2, Decimal("0.3308")),
    ]


async def test_an_unknown_id_is_absent_and_the_rest_are_answered(
    session: AsyncSession,
) -> None:
    """DoD: a partial answer. An id with no book is left out rather than an
    error, and the market beside it is still priced. A published market
    nobody has touched has no book either, and reads the same way: the batch
    opens none (DECISIONS.md, "The batch snapshot opens no book: a market
    without one is absent")."""
    known = Upstream()
    await warm(session, known)

    result = await _snapshot_service().list_snapshots(
        session, [uuid.uuid4(), known.market_id]
    )

    assert [snapshot.market_id for snapshot in result] == [known.market_id]


async def test_a_book_that_cannot_be_priced_is_absent_and_the_rest_are_answered(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "A book the batch snapshot cannot price is left out and
    logged". A book cut to one outcome row is the single snapshot's
    `market_book_incomplete`; here it costs only its own card."""
    damaged, healthy = Upstream(), Upstream()
    await warm(session, damaged)
    await warm(session, healthy)
    await drop_outcome_row(session, damaged.market_id, position=1)

    result = await _snapshot_service().list_snapshots(
        session, [damaged.market_id, healthy.market_id]
    )

    assert [snapshot.market_id for snapshot in result] == [healthy.market_id]


async def test_the_answer_follows_the_order_the_ids_were_sent(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "The batch snapshot reads every market in one statement,
    and answers in the order asked". Sent in descending id order, the reverse
    of the order the statement sorts its rows in."""
    markets = await _three_warm_markets(session)
    sent = sorted((market.market_id for market in markets), reverse=True)

    result = await _snapshot_service().list_snapshots(session, sent)

    assert [snapshot.market_id for snapshot in result] == sent


async def test_a_repeated_id_is_answered_once_where_it_first_appears(
    session: AsyncSession,
) -> None:
    """The same entry: duplicates are not an error, and each market is
    answered once."""
    first, second = Upstream(), Upstream()
    await warm(session, first)
    await warm(session, second)

    result = await _snapshot_service().list_snapshots(
        session, [second.market_id, first.market_id, second.market_id]
    )

    assert [snapshot.market_id for snapshot in result] == [
        second.market_id,
        first.market_id,
    ]


# =========================================================================
# The read it makes
# =========================================================================
async def test_one_statement_reads_every_market(session: AsyncSession) -> None:
    """No N+1: three markets cost one statement touching `market_books`, and it
    joins `market_outcomes`, so each `state_version` comes from the same read
    as the `q` it prices (D-013)."""
    markets = await _three_warm_markets(session)

    with capture_sql() as statements:
        await _snapshot_service().list_snapshots(
            session, [market.market_id for market in markets]
        )

    book_reads = mentioning(statements, "market_books")
    assert len(book_reads) == 1, f"expected one statement, sent:\n{statements}"
    assert "market_outcomes" in book_reads[0].lower(), book_reads[0]


async def test_the_batch_takes_no_lock(session: AsyncSession) -> None:
    """D-012: the read decides no write. A lock would queue a whole page of
    cards behind any one trade holding its book."""
    markets = await _three_warm_markets(session)

    with capture_sql() as statements:
        await _snapshot_service().list_snapshots(
            session, [market.market_id for market in markets]
        )

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking == [], f"the batch read must take no locks:\n{locking}"
