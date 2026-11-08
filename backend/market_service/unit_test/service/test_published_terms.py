"""The published markets' trading terms, as the ledger's copy reads them. #640

ADR 0030, the ledger's local copy. Every market that was ever published is
listed with its stored status and `close_time`; deriving whether it still
trades is the ledger's job, so nothing here asks the clock. HTTP wiring is in
unit_test/controller/test_internal_routes.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import MarketStatus
from service import market_service, published_terms
from unit_test.conftest import actor, close_request, draft_request, published_market


async def test_a_draft_and_a_submitted_market_are_absent_and_a_closed_one_is_present_with_its_stored_status(
    session: AsyncSession,
) -> None:
    creator = actor()
    await market_service.save(session, creator, draft_request())
    await market_service.save(session, creator, draft_request(status="submitted"))
    still_open = await published_market(session, creator)
    closed_early = await published_market(session, creator)
    closes_later = closed_early.close_time
    await market_service.close_early(session, creator, closed_early.id, close_request())

    rows = await published_terms.list_published_terms(session)

    terms = {row.id: (row.status, row.close_time) for row in rows}
    assert set(terms) == {still_open.id, closed_early.id}
    assert terms[still_open.id][0] is MarketStatus.OPEN
    # Stopped by hand, so its close time is still ahead: the ledger reads the
    # stored status to know it stopped, not the clock. ADR 0014.
    assert terms[closed_early.id] == (MarketStatus.CLOSED, closes_later)
    assert closes_later > datetime.now(UTC)


async def test_published_markets_are_listed_in_id_order(session: AsyncSession) -> None:
    creator = actor()
    published = [await published_market(session, creator) for _ in range(5)]

    rows = await published_terms.list_published_terms(session)

    assert [row.id for row in rows] == sorted(market.id for market in published)
