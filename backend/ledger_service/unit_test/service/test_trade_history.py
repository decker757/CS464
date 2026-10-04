"""The trade history's rows. [T-5] #25

Every row is one entry of the user's account, built from real grants and
trades. The figures are #23's worked example, pinned in `history_fixtures.py`.
The one exception is the unrecognised-kind test, which says so.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import terms_client_over
from unit_test.history_fixtures import (
    BUY_AVERAGE,
    BUY_COST,
    SELL_AVERAGE,
    SENT_AS_TEN,
    as_pairs,
    assert_average_rounding_is_load_bearing,
    expected_balances,
    grant_id_of,
    read_history,
    stored_context,
    trade_fields,
    traded,
    transaction_ids,
    user_entries,
    walk,
)
from unit_test.portfolio_fixtures import funded, market_at
from unit_test.sell_fixtures import HELD, PROCEEDS, hold, sell, version_of
from unit_test.trade_fixtures import (
    SMALL_QUANTITY,
    accounts_module,
    books,
    buy,
    entities,
    grants,
    posting,
    pricing,
    preview,
    token,
    transaction_count,
)

NULL_TRADE_FIELDS = (None, None, None, None, None)


# =========================================================================
# Which rows appear
# =========================================================================
async def test_every_grant_buy_and_sell_appears_once_newest_first(
    session: AsyncSession,
) -> None:
    scenario = await traded(session)

    page = await read_history(session, scenario.user_id)

    assert transaction_ids(page.rows) == [
        scenario.sell.transaction_id,
        scenario.buy.transaction_id,
        scenario.grant_id,
    ]
    assert transaction_ids(page.rows) == [
        transaction_id for _, transaction_id, _ in await user_entries(session, scenario.user_id)
    ]


async def test_a_replayed_trade_appears_once(session: AsyncSession) -> None:
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    first = await hold(session, upstream, user_id=user_id, client_key="replayed")

    replay = await buy(
        session,
        upstream,
        user_id=user_id,
        quantity=HELD,
        state_version=await version_of(session, upstream.market_id),
        client_key="replayed",
    )

    assert replay.transaction_id == first.transaction_id
    page = await read_history(session, user_id)
    assert transaction_ids(page.rows) == [
        first.transaction_id,
        await grant_id_of(session, user_id),
    ]


async def test_another_user_s_rows_and_a_market_seed_never_appear(
    session: AsyncSession,
) -> None:
    """The other user's grant lands before ours and their buys after, and a
    second market is seeded in between. A read that did not filter on the
    account, in the rows or in the running sum, shows them or is off by them.
    The other user trades in the first market only after our sell, so our
    figures stay the worked example's."""
    other_id, _ = await funded(session)
    scenario = await traded(session)
    second = await market_at(session)
    await hold(session, scenario.upstream, user_id=other_id, quantity=SMALL_QUANTITY)
    await hold(session, second, user_id=other_id)

    page = await read_history(session, scenario.user_id)

    assert transaction_ids(page.rows) == [
        scenario.sell.transaction_id,
        scenario.buy.transaction_id,
        scenario.grant_id,
    ]
    assert [row.balance_after for row in page.rows] == [
        scenario.after_sell,
        scenario.after_buy,
        scenario.credits,
    ]
    seeds = [
        (await posting().find_by_idempotency_key(session, books().market_open_key(m))).id
        for m in (scenario.upstream.market_id, second.market_id)
    ]
    assert not set(seeds) & set(transaction_ids(page.rows))


# =========================================================================
# balance_after
# =========================================================================
async def test_walking_every_page_each_balance_after_is_the_older_row_s_plus_its_amount(
    session: AsyncSession,
) -> None:
    """Five rows at `limit=2`: three pages, so two page boundaries."""
    scenario = await traded(session)
    await hold(session, scenario.upstream, user_id=scenario.user_id, quantity=SMALL_QUANTITY, outcome=1)
    await sell(session, scenario.upstream, user_id=scenario.user_id, quantity=Decimal("5.0000"), outcome=1)

    pages = await walk(session, scenario.user_id, limit=2)
    rows = [row for page in pages for row in page]

    assert len(pages) == 3
    assert as_pairs(rows) == await expected_balances(session, scenario.user_id)
    for newer, older in zip(rows, rows[1:]):
        assert newer.balance_after == older.balance_after + newer.entry.amount
    assert rows[-1].balance_after == rows[-1].entry.amount == scenario.credits


# =========================================================================
# The trade fields
# =========================================================================
async def test_a_buy_row_carries_its_market_outcome_side_quantity_and_average_price(
    session: AsyncSession,
) -> None:
    assert_average_rounding_is_load_bearing()
    scenario = await traded(session)

    row = (await read_history(session, scenario.user_id)).rows[1]

    assert row.entry.transaction_id == scenario.buy.transaction_id
    assert row.entry.amount == -BUY_COST
    assert row.balance_after == scenario.after_buy
    assert trade_fields(row) == (
        scenario.upstream.market_id,
        scenario.upstream.outcomes[0],
        pricing().Side.BUY,
        HELD,
        BUY_AVERAGE,
    )
    assert str(row.trade.quantity) == "54.3333"
    assert str(row.trade.average_price) == "0.5493"


async def test_a_sell_row_carries_positive_proceeds_and_its_average_price(
    session: AsyncSession,
) -> None:
    assert_average_rounding_is_load_bearing()
    scenario = await traded(session)

    row = (await read_history(session, scenario.user_id)).rows[0]

    assert row.entry.transaction_id == scenario.sell.transaction_id
    assert row.entry.amount == PROCEEDS
    assert row.balance_after == scenario.after_sell
    assert trade_fields(row) == (
        scenario.upstream.market_id,
        scenario.upstream.outcomes[0],
        pricing().Side.SELL,
        Decimal("10.0000"),
        SELL_AVERAGE,
    )
    assert str(row.trade.average_price) == "0.5891"


async def test_quantity_sent_as_10_is_shown_as_10_0000(session: AsyncSession) -> None:
    """`context` keeps the quantity exactly as it arrived; the row shows it at
    scale 4 whatever the trader sent."""
    scenario = await traded(session)

    row = (await read_history(session, scenario.user_id)).rows[0]

    assert (await stored_context(session, scenario.sell.transaction_id))["quantity"] == "10"
    assert str(row.trade.quantity) == "10.0000"


@pytest.mark.parametrize("side", ["buy", "sell"])
async def test_average_price_equals_the_preview_s_at_the_same_state_version(
    session: AsyncSession, side: str
) -> None:
    upstream = await market_at(session)
    user_id, _ = await funded(session)
    if side == "sell":
        await hold(session, upstream, user_id=user_id)
    quantity = HELD if side == "buy" else SENT_AS_TEN

    quote = await preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[0],
        side=pricing().Side(side),
        quantity=quantity,
        access_token=token(),
        terms_client=terms_client_over(upstream.transport),
    )
    await buy(
        session,
        upstream,
        user_id=user_id,
        quantity=quantity,
        side=side,
        state_version=quote.state_version,
        client_key=f"parity-{side}",
    )

    row = (await read_history(session, user_id)).rows[0]
    assert row.trade.average_price == quote.average_price
    assert row.trade.average_price == (BUY_AVERAGE if side == "buy" else SELL_AVERAGE)


# =========================================================================
# Rows with no trade
# =========================================================================
async def test_a_brand_new_user_s_first_read_mints_one_grant_row_with_the_trade_fields_null(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    key = grants().grant_key(user_id)
    assert await transaction_count(session, key=key) == 0

    page = await read_history(session, user_id)

    assert len(page.rows) == 1
    (row,) = page.rows
    assert row.entry.transaction_id == await grant_id_of(session, user_id)
    assert row.entry.amount == starting_credits
    assert row.balance_after == starting_credits
    assert trade_fields(row) == NULL_TRADE_FIELDS
    assert await transaction_count(session, key=key) == 1


async def test_an_unrecognised_kind_renders_with_amount_and_balance_after_and_null_trade_fields(
    session: AsyncSession,
) -> None:
    """**The one test that fabricates a kind.** A `market_seed` posted against
    a user account: no real writer does that, and it stands in for a kind the
    row builder does not recognise, such as a settlement. Its context names a
    `market_id`, so a builder that reads any kind's context for one is caught.
    """
    user_id, credits = await funded(session)
    amount = Decimal("12.3457")
    ents = entities()
    user = await accounts_module().ensure(session, ents.AccountKind.USER, user_id)
    platform = await accounts_module().ensure_platform(session)
    fabricated = await posting().post(
        session,
        idempotency_key=f"fabricated-kind:{uuid.uuid4()}",
        kind=ents.TransactionKind.MARKET_SEED,
        legs=[
            posting().Leg(account=platform, amount=-amount),
            posting().Leg(account=user, amount=amount),
        ],
        context={"market_id": str(uuid.uuid4())},
    )

    rows = (await read_history(session, user_id)).rows

    assert transaction_ids(rows) == [fabricated.id, await grant_id_of(session, user_id)]
    assert rows[0].entry.amount == amount
    assert rows[0].balance_after == credits + amount
    assert trade_fields(rows[0]) == NULL_TRADE_FIELDS
