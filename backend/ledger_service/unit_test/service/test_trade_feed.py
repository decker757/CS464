"""A market's trade feed, without HTTP. [BE][2.4] #63

What the service owns: which entries are the market's trades, their order,
and the keyset paging over them. DECISIONS.md, "The market's trade feed reads
the pool account's feed, filtered to the trade kinds, and its rows are built
by `trade_fields_of`". The wire shape and the guard are
`controller/test_trade_feed_routes.py`'s.

**The interface is agreed here, and this is the single place it is written
down.** `service/ledger_service.py`, beside `trade_fields_of` and
`history_for_user`, gains

    async def list_market_trades(
        session: AsyncSession,
        market_id: uuid.UUID,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> TradePage

`history_for_user`'s parameters with `market_id` in place of `user_id`. The
page mirrors `EntryPage`: `rows`, `next_cursor`, and `has_more` as
`next_cursor is not None`. A row cannot be a `HistoryRow`, which carries no
trader and a `balance_after` the feed refuses, so each row carries the wire
row's fields as attributes; these tests read `transaction_id` and `user_id`.
It raises `MalformedCursor` and writes nothing. If the implementation lands on
other names, `_list_market_trades` below is the one place to change.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.history_fixtures import ledger_service
from unit_test.sell_fixtures import hold
from unit_test.trade_feed_fixtures import feed, settled_feed
from unit_test.trade_fixtures import SMALL_QUANTITY


async def _list_market_trades(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    limit: int = 50,
    cursor: str | None = None,
):
    return await ledger_service().list_market_trades(
        session, market_id, limit=limit, cursor=cursor
    )


def _ids(rows) -> list[uuid.UUID]:
    return [row.transaction_id for row in rows]


def _ids_of_results(results) -> list[uuid.UUID]:
    return [result.transaction_id for result in results]


async def test_the_feed_lists_the_market_s_trades_newest_first(
    session: AsyncSession,
) -> None:
    """Catches: oldest first; a filter on the reader rather than the market
    (there is no reader here, and the market's traders are two)."""
    scenario = await feed(session)

    page = await _list_market_trades(session, scenario.m1.market_id)

    assert _ids(page.rows) == _ids_of_results(scenario.m1_newest_first)
    assert page.next_cursor is None
    assert page.has_more is False


async def test_each_row_names_its_trader(session: AsyncSession) -> None:
    """DECISIONS.md, "A market's trades carry `user_id`…". Catches: the pool
    account's owner, or one trader's id on every row."""
    scenario = await feed(session)

    page = await _list_market_trades(session, scenario.m1.market_id)

    assert [row.user_id for row in page.rows] == [
        scenario.trader_b,
        scenario.trader_a,
        scenario.trader_a,
    ]


async def test_each_market_s_feed_holds_only_its_own_trades(
    session: AsyncSession,
) -> None:
    """Catches: no market filter, or the wrong pool account."""
    scenario = await feed(session)

    m1 = await _list_market_trades(session, scenario.m1.market_id)
    m2 = await _list_market_trades(session, scenario.m2.market_id)

    assert _ids(m1.rows) == _ids_of_results(scenario.m1_newest_first)
    assert _ids(m2.rows) == [scenario.m2_buy.transaction_id]


async def test_the_seed_the_payout_and_the_residue_are_left_out(
    session: AsyncSession,
) -> None:
    """"Every row is a trade": the pool also holds `market_seed`,
    `settlement` and `settlement_residue`, and a settled market still reads.
    Catches: no kind filter, or one that admits the settlement kinds; a
    status check on the market."""
    scenario = await settled_feed(session)

    page = await _list_market_trades(session, scenario.upstream.market_id)

    assert _ids(page.rows) == _ids_of_results(
        [scenario.loser_buy, scenario.winner_buy]
    )
    for left_out in scenario.left_out:
        assert left_out not in _ids(page.rows)


async def test_paging_one_at_a_time_visits_every_trade_once(
    session: AsyncSession,
) -> None:
    """At `limit=1` over a pool whose newest entries are the residue and the
    payout and whose oldest is the seed. Catches: offset paging; the kind
    filter applied after the LIMIT, which serves empty pages or a wrong
    `has_more`."""
    scenario = await settled_feed(session)
    market_id = scenario.upstream.market_id

    pages = []
    cursor: str | None = None
    while True:
        page = await _list_market_trades(session, market_id, limit=1, cursor=cursor)
        assert page.has_more == (page.next_cursor is not None)
        pages.append(page)
        cursor = page.next_cursor
        if cursor is None:
            break

    for page in pages[:-1]:
        assert len(page.rows) == 1
    walked = [transaction_id for page in pages for transaction_id in _ids(page.rows)]
    assert walked == _ids_of_results([scenario.loser_buy, scenario.winner_buy])


async def test_a_trade_committed_between_pages_does_not_shift_the_next_page(
    session: AsyncSession,
) -> None:
    """"A trade committed while you page is newer than every row on screen, so
    it cannot shift a later page." Catches: OFFSET instead of keyset, which
    repeats the last row of page 1 at the top of page 2."""
    scenario = await feed(session)
    market_id = scenario.m1.market_id

    page_one = await _list_market_trades(session, market_id, limit=2)
    newer = await hold(
        session, scenario.m1, user_id=scenario.trader_b, quantity=SMALL_QUANTITY
    )
    page_two = await _list_market_trades(
        session, market_id, limit=2, cursor=page_one.next_cursor
    )

    assert _ids(page_one.rows) == _ids_of_results(scenario.m1_newest_first[:2])
    assert _ids(page_two.rows) == [scenario.a_buy.transaction_id]
    assert newer.transaction_id not in _ids(page_two.rows)
    assert page_two.next_cursor is None
