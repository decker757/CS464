"""The price event a trade puts on the bus. [T-2] #22, [F-9] #112, ADR 0010

[F-9] #112 shipped the producer with no caller and a test asserting there was
none. This ticket is the caller, so what is left to hold is the three things
the primitive could not hold on its own: that a committed trade publishes
**once**, that the publish happens **after** the commit, and that a publish
that fails does not turn a trade that has already charged somebody into a
failure.

Everything else about the producer — the channel, the payload's shape, the
swallow, the log line, the `CancelledError` that is not caught — belongs to
`test_price_publish.py` and is not re-decided or re-asserted here.

**`test_price_publish.py::test_nothing_in_this_service_calls_publish` is
deleted by this ticket**, as its own docstring and #22's issue both say. It
held the "primitive before caller" shape for exactly one ticket and is false
the moment this file's first test passes.

**"After the commit" is asserted rather than asserted-about.** The recorder
runs a callback while the publish is in flight, from a session of its own.
Under READ COMMITTED that session can see the trade only if it has already
committed, so the ordering claim becomes a row that is either there or is
not. Ordering two timestamps would prove less and would pass on a publish
issued from inside the transaction.
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
    fund,
    lmsr,
    q_of,
    session_factory,
    trading,
    transaction_count,
    warm,
)


def _bus():
    from service import bus  # noqa: PLC0415

    return bus


def _expected_prices(q: list[Decimal]) -> list[str]:
    """Every outcome's price at `q`, quantized the way the wire wants it.

    `ROUND_HALF_UP`, with no direction to favour — nobody is charged a price,
    unlike `core/pricing.py::quantize_cost`'s directional rounding of a cost.
    Recomputed from `core/lmsr.py` rather than pinned, the same rule the rest
    of this suite follows.
    """
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
    """One trade moves the market once, so it announces once.

    Twice would put a duplicate on the channel for one `state_version`,
    harmless only because `realtime_service/service/ordering.py` drops it —
    and a guard that starts absorbing a producer's duplicates by default has
    stopped being defensive.
    """
    upstream = Upstream()
    recorder = Recorder()
    await warm(session, upstream)

    await _trade(session, upstream, recorder)

    assert len(recorder.calls) == 1
    assert recorder.calls[0][0] == _bus().PRICE_CHANNEL


async def test_the_event_carries_the_post_trade_prices_for_every_outcome(
    session: AsyncSession,
) -> None:
    """The prices the trade left behind, not the ones it was quoted against.

    Every outcome, because a trade moves the whole softmax and a client
    rendering only the traded one would show a market whose prices no longer
    sum to one. Compared against `core/lmsr.py` evaluated at the post-trade
    `q`, so an event built from the pre-trade vector — the easy mistake, since
    that vector is the one already in hand when the cost is computed — fails
    here.
    """
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
    """What goes on the channel is what the consumer validates with
    `extra="forbid"` over four fields. Parsed back through the producer's own
    model, so a trade that assembled the payload by hand — with an extra field
    the relay would drop the whole event over — fails here rather than in
    production, silently, as prices that stop arriving."""
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
    """ADR 0010's one unbendable rule, as a row that is either visible or not.

    Publishing first announces a price that a rollback then un-makes, and
    nothing acknowledges or subscribes on the producer's behalf, so there is
    no second chance to correct it. The callback below runs while the publish
    is in flight and asks a **separate** session whether the trade is there.
    Under READ COMMITTED it can only be there if the commit already happened.

    The crash window between the commit and the publish is real, accepted and
    named in ADR 0010 — a client that reconnects fetches a snapshot, which is
    the same recovery path [X-4] #37 already requires. This test is about the
    ordering, not about closing that window.
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
async def test_a_publish_that_fails_does_not_fail_the_trade(
    session: AsyncSession,
) -> None:
    """The trade has committed by the time the publish runs, so the exception
    has nowhere useful to go.

    Turning a committed trade into a 500 would tell a trader their trade
    failed when it had charged them, which is strictly worse than a stale
    price on a screen that is about to reconcile. The swallow itself lives in
    `service/bus.py` and is `test_price_publish.py`'s; what this holds is that
    the trade path did not wrap it in something that re-raises.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    await fund(session, user_id)

    recorder = Recorder(raises=ConnectionError("redis is unreachable"))
    trade = await buy(session, upstream, user_id=user_id, redis_client=recorder)

    assert trade.state_version == 1
    assert len(recorder.calls) == 1


async def test_a_trade_whose_publish_failed_is_still_fully_written(
    session: AsyncSession,
) -> None:
    """The other half, and the one that would catch a "publish, then commit"
    ordering hidden behind a swallow.

    If the publish were inside the transaction and its failure were caught,
    this would still return — and the trade might still be there. What pins
    the ordering is that everything the trade writes is committed and correct
    after a publish that threw.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await warm(session, upstream)
    credits = await fund(session, user_id)

    recorder = Recorder(raises=ConnectionError("redis is unreachable"))
    trade = await buy(session, upstream, user_id=user_id, redis_client=recorder)

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
    """A price event says a market moved. A refused trade moved nothing.

    Three different refusals, because the three leave the request at three
    different depths — the gate before the lock, the staleness check under it,
    and the overdraft check inside `post` with the book writes already pending
    in the session. A publish wired anywhere but after a successful commit
    shows up in at least one of them.
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
        quantity = Decimal("1111.1111")
        expected = errors().InsufficientFunds
        assert credits < quantity, "this quantity has to be unaffordable"

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
