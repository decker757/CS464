"""The price event a trade puts on the bus. [T-2] #22, [F-9] #112, ADR 0010

What the caller owes the producer: a committed trade publishes once, after the
commit, and a failed publish never fails the trade. The producer itself is
`test_price_publish.py`'s. "After the commit" is checked from a second session
while the publish is in flight: under READ COMMITTED it sees the trade only if
the commit already happened.
"""

from __future__ import annotations

import json
import uuid
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.trade_fixtures import (
    B,
    QUANTITY,
    QUANTUM,
    Q,
    Recorder,
    Upstream,
    assert_ledger_balances,
    balance_of_user,
    book_row,
    buy,
    entities,
    entry_count,
    errors,
    expected_total,
    fund,
    lmsr,
    q_of,
    session_factory,
    trading,
    transaction_count,
    unaffordable,
    warm,
)


def _bus():
    from service import bus  # noqa: PLC0415

    return bus


def _expected_prices(q: list[Decimal]) -> list[str]:
    """Every outcome's price at `q`, recomputed from `core/lmsr.py` and
    rounded half up: nobody is charged a price."""
    return [
        str(price.quantize(QUANTUM, rounding=ROUND_HALF_UP))
        for price in lmsr().prices(q, B)
    ]


async def _trade(session: AsyncSession, upstream: Upstream, recorder: Recorder):
    user_id = uuid.uuid4()
    await fund(session, user_id)
    return await buy(session, upstream, user_id=user_id, redis_client=recorder)


# =========================================================================
# One trade, one event
# =========================================================================
async def test_a_committed_trade_publishes_exactly_once(
    session: AsyncSession,
) -> None:
    """One trade moves the market once, so it announces once."""
    upstream = Upstream()
    recorder = Recorder()
    await warm(session, upstream)

    await _trade(session, upstream, recorder)

    assert len(recorder.calls) == 1
    assert recorder.calls[0][0] == _bus().PRICE_CHANNEL


async def test_the_event_carries_the_post_trade_prices_for_every_outcome(
    session: AsyncSession,
) -> None:
    """The prices the trade left behind, for every outcome, not the pre-trade
    ones already in hand."""
    upstream = Upstream()
    recorder = Recorder()
    await warm(session, upstream)

    await _trade(session, upstream, recorder)

    payload = json.loads(recorder.calls[0][1])
    after = [Q[0] + QUANTITY, Q[1]]
    assert [p["price"] for p in payload["prices"]] == _expected_prices(after)
    assert [p["position"] for p in payload["prices"]] == [0, 1]
    assert [p["outcome_id"] for p in payload["prices"]] == [
        str(o) for o in upstream.outcomes
    ]
    assert _expected_prices(after) != _expected_prices(list(Q)), (
        "the trade did not move the prices enough for this assertion to "
        "distinguish the post-trade vector from the pre-trade one"
    )


async def test_the_event_carries_the_new_state_version(
    session: AsyncSession,
) -> None:
    """D-011's counter, post-trade. It is the field the relay orders on, so an
    event carrying the version the trade was quoted against would be dropped
    by the consumer as one it had already seen."""
    upstream = Upstream()
    recorder = Recorder()
    await warm(session, upstream)

    trade = await _trade(session, upstream, recorder)

    payload = json.loads(recorder.calls[0][1])
    assert payload["state_version"] == trade.state_version == 1
    assert payload["market_id"] == str(upstream.market_id)


async def test_the_event_validates_as_a_price_event(
    session: AsyncSession,
) -> None:
    """The payload parses back through `PriceEvent`, as the consumer, which
    forbids extra fields, will parse it."""
    upstream = Upstream()
    recorder = Recorder()
    await warm(session, upstream)

    await _trade(session, upstream, recorder)

    from model.schemas import PriceEvent  # noqa: PLC0415

    event = PriceEvent.model_validate_json(recorder.calls[0][1])
    assert event.market_id == upstream.market_id
    assert len(event.prices) == 2


# =========================================================================
# After the commit
# =========================================================================
async def test_the_publish_happens_after_the_transaction_has_committed(
    session: AsyncSession,
) -> None:
    """ADR 0010: publish after the commit, or a rollback un-makes an announced
    price. Checked from a separate session while the publish is in flight.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    recorder = Recorder()
    seen: dict[str, object] = {}
    key = trading().trade_key(user_id, upstream.market_id, "client-key-1")

    async def look_from_another_session() -> None:
        transaction = entities().Transaction
        async with session_factory()() as other:
            seen["committed"] = (
                await other.execute(
                    select(transaction).where(transaction.idempotency_key == key)
                )
            ).scalar_one_or_none() is not None
            seen["q"] = list(
                (
                    await other.execute(
                        select(entities().MarketOutcome.q)
                        .where(entities().MarketOutcome.market_id == upstream.market_id)
                        .order_by(entities().MarketOutcome.position)
                    )
                ).scalars()
            )

    recorder.on_publish = look_from_another_session

    await buy(session, upstream, user_id=user_id, redis_client=recorder)

    assert seen.get("committed") is True, (
        "the price was published before the trade committed — a rollback "
        "after this point announces a price that never happened"
    )
    assert seen["q"] == [Q[0] + QUANTITY, Q[1]], (
        "the transaction was visible but the book was not, so the publish "
        "landed between two commits rather than after one"
    )


# =========================================================================
# A publish that fails
# =========================================================================
async def test_a_trade_whose_publish_failed_is_still_fully_written(
    session: AsyncSession,
) -> None:
    """A publish was attempted and threw, and everything the trade wrote is
    still committed and correct, which catches a "publish, then commit"
    hidden behind a swallow.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    credits = await fund(session, user_id)

    recorder = Recorder(raises=ConnectionError("redis is unreachable"))
    trade = await buy(session, upstream, user_id=user_id, redis_client=recorder)

    assert len(recorder.calls) == 1, "the trade never attempted its publish"
    async with session_factory()() as other:
        assert await q_of(other, upstream.market_id) == [Q[0] + QUANTITY, Q[1]]
        assert (await book_row(other, upstream.market_id)).state_version == 1
        assert await balance_of_user(other, user_id) == credits + trade.total
        await assert_ledger_balances(other)


# =========================================================================
# Nothing to announce
# =========================================================================
@pytest.mark.parametrize(
    "break_it",
    ["closed", "stale", "poor"],
    ids=["market_closed", "quote_stale", "insufficient_funds"],
)
async def test_a_refused_trade_publishes_nothing(
    session: AsyncSession, break_it: str
) -> None:
    """A refused trade moved nothing, so it publishes nothing. Three refusals
    at three depths: before the lock, under it, and inside `post`.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    credits = await fund(session, user_id)
    before = await entry_count(session)

    recorder = Recorder()
    quantity = QUANTITY
    version = 0

    if break_it == "closed":
        upstream.closes()
        expected = errors().MarketClosed
    elif break_it == "stale":
        version = 9
        expected = errors().QuoteStale
    else:
        quantity = unaffordable(credits)
        expected = errors().InsufficientFunds
        assert -expected_total(Q, 0, "buy", quantity) > credits, (
            "this quantity has to be unaffordable"
        )

    with pytest.raises(expected):
        await buy(
            session,
            upstream,
            user_id=user_id,
            quantity=quantity,
            state_version=version,
            redis_client=recorder,
        )
    await session.rollback()

    assert recorder.calls == []
    assert await entry_count(session) == before
    assert await transaction_count(
        session, key=trading().trade_key(user_id, upstream.market_id, "client-key-1")
    ) == 0
