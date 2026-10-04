"""A sell's price event. [T-3] #23, ADR 0010

The same producer contract as a buy's, which `test_trade_publish.py` holds:
once, after the commit, every outcome's post-trade price, nothing on a
refusal or a replay, and a publish failure never fails the trade. What a sell
adds is the direction: **the sold outcome's price goes down and every other
outcome's goes up**, checked against the published event and against the
snapshot taken before the trade.

Three outcomes, so "every other" is more than one, and a second trader holds
some of outcome 1 so the two unsold outcomes start at different prices — a bug
that moved only one of them cannot hide behind two equal numbers.
"""

from __future__ import annotations

import json
import uuid
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.sell_fixtures import (
    HELD,
    REMAINING_QUANTITY,
    SOLD,
    expected_proceeds,
    hold,
    holding,
    open_at_zero,
    q_now,
    remaining_basis,
    sell,
    snapshot_module,
    version_of,
)
from unit_test.trade_fixtures import (
    B,
    QUANTUM,
    SMALL_QUANTITY,
    Recorder,
    Upstream,
    assert_ledger_balances,
    balance_of_user,
    entities,
    expected_total,
    fund,
    lmsr,
    session_factory,
    token,
    trading,
)


def _bus():
    from service import bus  # noqa: PLC0415

    return bus


def _wire(q: list[Decimal]) -> list[str]:
    return [
        str(p.quantize(QUANTUM, rounding=ROUND_HALF_UP)) for p in lmsr().prices(q, B)
    ]


async def _market(session: AsyncSession):
    """Three outcomes at zero; the seller holds `54.3333` of outcome 0 and a
    second trader `13.3333` of outcome 1. `q = [54.3333, 13.3333, 0]`."""
    upstream = Upstream(outcomes=3)
    seller, other = uuid.uuid4(), uuid.uuid4()
    await open_at_zero(session, upstream)
    await fund(session, seller)
    await fund(session, other)
    await hold(session, upstream, user_id=seller)
    await hold(session, upstream, user_id=other, quantity=SMALL_QUANTITY, outcome=1)
    return upstream, seller


_BEFORE = [HELD, SMALL_QUANTITY, Decimal(0)]
_AFTER = [HELD - SOLD, SMALL_QUANTITY, Decimal(0)]


async def test_a_committed_sell_publishes_once_with_every_post_trade_price(
    session: AsyncSession,
) -> None:
    upstream, seller = await _market(session)
    recorder = Recorder()

    trade = await sell(session, upstream, user_id=seller, redis_client=recorder)

    assert len(recorder.calls) == 1
    channel, raw = recorder.calls[0]
    assert channel == _bus().PRICE_CHANNEL
    payload = json.loads(raw)
    assert payload["state_version"] == trade.state_version == 3
    assert [p["position"] for p in payload["prices"]] == [0, 1, 2]
    assert [p["price"] for p in payload["prices"]] == _wire(_AFTER)
    assert _wire(_AFTER) != _wire(_BEFORE)


async def test_selling_moves_the_sold_price_down_and_every_other_up(
    session: AsyncSession,
) -> None:
    """Against the snapshot taken before the sell, not a recomputation of it:
    what a client was looking at, and what it is then told."""
    upstream, seller = await _market(session)
    before = await snapshot_module().snapshot(
        session, upstream.market_id, access_token=token(), transport=upstream.transport
    )
    recorder = Recorder()

    await sell(session, upstream, user_id=seller, redis_client=recorder)

    after = {
        p["position"]: Decimal(p["price"])
        for p in json.loads(recorder.calls[0][1])["prices"]
    }
    was = {p.position: p.price for p in before.prices}

    assert before.state_version == 2
    assert after[0] < was[0], "the sold outcome's price did not fall"
    assert after[1] > was[1], "an unsold outcome's price did not rise"
    assert after[2] > was[2], "an unsold outcome's price did not rise"


async def test_the_publish_happens_after_the_sell_has_committed(
    session: AsyncSession,
) -> None:
    upstream, seller = await _market(session)
    recorder = Recorder()
    seen: dict[str, object] = {}
    key = trading().trade_key(seller, upstream.market_id, "after-commit")

    async def look_from_another_session() -> None:
        transaction = entities().Transaction
        async with session_factory()() as other:
            seen["committed"] = (
                await other.execute(
                    select(transaction).where(transaction.idempotency_key == key)
                )
            ).scalar_one_or_none() is not None
            seen["q"] = await q_now(other, upstream.market_id)

    recorder.on_publish = look_from_another_session

    await sell(
        session, upstream, user_id=seller, client_key="after-commit", redis_client=recorder
    )

    assert seen.get("committed") is True
    assert seen["q"] == _AFTER


async def test_a_failed_publish_does_not_fail_the_sell(session: AsyncSession) -> None:
    """Everything the sell writes is committed and correct after a publish
    that threw. Three outcomes, so the figures are this book's own rather
    than the two-outcome worked example's."""
    upstream, seller = await _market(session)
    before = await balance_of_user(session, seller)
    basis = -expected_total([Decimal(0)] * 3, 0, "buy", HELD)
    proceeds = expected_proceeds(_BEFORE, 0, SOLD)
    recorder = Recorder(raises=ConnectionError("redis is unreachable"))

    trade = await sell(session, upstream, user_id=seller, redis_client=recorder)

    assert trade.total == proceeds
    assert len(recorder.calls) == 1
    async with session_factory()() as other:
        assert await q_now(other, upstream.market_id) == _AFTER
        assert await version_of(other, upstream.market_id) == 3
        assert await holding(
            other, seller, upstream.market_id, upstream.outcomes[0]
        ) == (REMAINING_QUANTITY, remaining_basis(basis, HELD, SOLD))
        assert await balance_of_user(other, seller) == before + proceeds
        await assert_ledger_balances(other)


@pytest.mark.parametrize(
    "refusal", ["held", "stale", "closed"], ids=["insufficient_shares_held", "quote_stale", "market_closed"]
)
async def test_a_refused_sell_publishes_nothing(
    session: AsyncSession, refusal: str
) -> None:
    from unit_test.sell_fixtures import insufficient_shares_held  # noqa: PLC0415
    from unit_test.trade_fixtures import errors  # noqa: PLC0415

    upstream, seller = await _market(session)
    recorder = Recorder()
    kwargs: dict[str, object] = {}
    expected: type[Exception]
    if refusal == "held":
        kwargs["quantity"] = HELD + SOLD
        expected = insufficient_shares_held()
    elif refusal == "stale":
        kwargs["state_version"] = 0
        expected = errors().QuoteStale
    else:
        upstream.closes()
        expected = errors().MarketClosed

    with pytest.raises(expected):
        await sell(session, upstream, user_id=seller, redis_client=recorder, **kwargs)
    await session.rollback()

    assert recorder.calls == []
