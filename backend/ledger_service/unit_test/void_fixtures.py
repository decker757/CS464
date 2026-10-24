"""Fixtures the [BE] #221 void tests share. ADR 0025.

A stand-in market_service that answers the public detail, the void step and
the settle step; real trades through the trade path, so the void's replay
reads the pool's feed exactly as production writes it; and the readers the
tests count with. Project modules are reached through functions, so a missing
name fails each test on its own rather than collection (D-007).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import mint_token, terms_client_over
from unit_test.sell_fixtures import version_of
from unit_test.settlement_fixtures import _audit_rows, _audit_table, admin, admin_token
from unit_test.trade_fixtures import books, buy, entities

ZERO = Decimal(0)
REASON = "The match was abandoned after ten overs; there is no result."

# The market most tests trade in: `b = 10` and a seed of 2, small enough that
# one trader's honest profit can run past it, which the cap needs.
B = Decimal("10.0000")
SEED = Decimal("2.0000")


def voiding():
    """`service/voiding.py`, the void's own module."""
    from service import voiding  # noqa: PLC0415

    return voiding


class VoidUpstream:
    """A stand-in market_service for the void: the public detail, the void step
    and the settle step, each counted.

    Open for trading until `closes`, which is what an early close does: the
    status moves and `voidable` turns true. `on_void` takes the 1-based
    number of the void call and may await, which is where a test watches what
    the caller holds while the call is in flight.
    """

    def __init__(
        self, *, b: Decimal = B, seed: Decimal = SEED, outcomes: int = 2
    ) -> None:
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]
        self.gets = 0
        self.voids = 0
        self.settles = 0
        self.void_tokens: list[str] = []
        self.void_reasons: list[str] = []
        self.get_status_code = 200
        self.void_status_code = 200
        self.get_raises: Exception | None = None
        self.void_raises: Exception | None = None
        self.on_void: Callable[[int], Awaitable[None]] | None = None
        self.body: dict[str, Any] = {
            "id": str(self.market_id),
            "status": "open",
            "close_time": "2027-01-05T12:00:00Z",
            "resolution_time": "2027-01-20T12:00:00Z",
            "liquidity_b": str(b),
            "seed_subsidy": str(seed),
            "published_at": "2026-09-01T09:00:00Z",
            "proposed_outcome_id": None,
            "settleable": False,
            "voidable": False,
            "outcomes": [
                {"id": str(o), "position": i, "label": f"Outcome {i}"}
                for i, o in enumerate(self.outcomes)
            ],
        }

    def closes(self) -> None:
        """Closed early, by its stored status, and so voidable."""
        self.body.update(status="closed", voidable=True)

    def reset_counts(self) -> None:
        self.gets = self.voids = self.settles = 0
        self.void_tokens.clear()
        self.void_reasons.clear()

    @property
    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "GET" and path == f"/public/markets/{self.market_id}":
                self.gets += 1
                if self.get_raises is not None:
                    raise self.get_raises
                return httpx.Response(self.get_status_code, json=self.body)

            if request.method == "POST" and path == f"/markets/{self.market_id}/void":
                self.voids += 1
                self.void_tokens.append(
                    request.headers.get("Authorization", "").removeprefix("Bearer ")
                )
                self.void_reasons.append(json.loads(request.content)["reason"])
                if self.on_void is not None:
                    await self.on_void(self.voids)
                if self.void_raises is not None:
                    raise self.void_raises
                return httpx.Response(self.void_status_code, json={})

            if request.method == "POST" and path == f"/markets/{self.market_id}/settle":
                self.settles += 1
                return httpx.Response(200, json={})

            raise AssertionError(f"unexpected call {request.method} {request.url}")

        return httpx.MockTransport(handler)

    @property
    def gate_open(self) -> httpx.MockTransport:
        """The same market, answering `open`: the trade's gate stubbed open, so
        only the latch and the book lock can refuse a trade. Counts nothing."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={**self.body, "status": "open"})

        return httpx.MockTransport(handler)


def gate_open_view(upstream: VoidUpstream) -> SimpleNamespace:
    """The market as a placement sees it with its gate stubbed open: the same
    ids, and `gate_open` as its transport, for `placement_fixtures.place`,
    which reads only these three."""
    return SimpleNamespace(
        market_id=upstream.market_id,
        outcomes=upstream.outcomes,
        transport=upstream.gate_open,
    )


async def open_book(session: AsyncSession, upstream: VoidUpstream):
    """The book opened at `q = 0` and funded with the seed, committed. Counts
    reset, so a test counts only its own calls."""
    book = await books().ensure_open(
        session,
        upstream.market_id,
        access_token=mint_token(uuid.uuid4()),
        terms_client=terms_client_over(upstream.transport),
    )
    upstream.reset_counts()
    return book


async def trade(
    session: AsyncSession,
    upstream: VoidUpstream,
    user_id: uuid.UUID,
    *,
    outcome: int,
    side: str,
    quantity: str,
):
    """One real trade through `trading.execute`, at the book's current
    version, so it lands on the pool's feed as production writes it."""
    result = await buy(
        session,
        upstream,
        user_id=user_id,
        outcome=outcome,
        side=side,
        quantity=Decimal(quantity),
        state_version=await version_of(session, upstream.market_id),
        client_key=f"void-test-{uuid.uuid4()}",
    )
    upstream.reset_counts()
    return result


async def void(
    session: AsyncSession,
    upstream: VoidUpstream,
    *,
    actor=None,  # noqa: ANN001
    reason: str = REASON,
    access_token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **extra: Any,
):
    """One void through `voiding.void_market`, with the administrator's own
    token forwarded, as the route calls it: `access_token`, or one minted for
    `actor`."""
    actor = actor if actor is not None else admin()
    return await voiding().void_market(
        session,
        upstream.market_id,
        actor=actor,
        reason=reason,
        access_token=access_token if access_token is not None else admin_token(actor),
        terms_client=terms_client_over(
            upstream.transport if transport is None else transport
        ),
        **extra,
    )


def refund_key(market_id: uuid.UUID, user_id: uuid.UUID, outcome_id: uuid.UUID) -> str:
    """Restated, not imported: the key is the portfolio's join condition, so a
    change to it must turn a test red rather than follow along."""
    return f"void-refund:{market_id}:{user_id}:{outcome_id}"


def residue_key(market_id: uuid.UUID) -> str:
    return f"void-residue:{market_id}"


async def refunds_by_holder(session: AsyncSession, market_id: uuid.UUID) -> dict:
    """`{(user_id, outcome_id): amount}` over the market's committed
    `void_refund` transactions, read off the holder's leg."""
    ents = entities()
    session.expire_all()
    rows = (
        await session.execute(
            select(ents.Transaction.context, ents.Entry.amount)
            .join(ents.Entry, ents.Entry.transaction_id == ents.Transaction.id)
            .join(ents.Account, ents.Account.id == ents.Entry.account_id)
            .where(
                ents.Transaction.kind == ents.TransactionKind.VOID_REFUND,
                ents.Account.kind == ents.AccountKind.USER,
            )
        )
    ).all()
    return {
        (uuid.UUID(context["user_id"]), uuid.UUID(context["outcome_id"])): amount
        for context, amount in rows
        if context["market_id"] == str(market_id)
    }


async def voided_entries(reader: AsyncSession, *actor_ids: uuid.UUID) -> list:
    """`market.voided` entries by these actors, read as `audit_svc`."""
    table = _audit_table()
    return await _audit_rows(
        reader,
        table.c.action_type == "market.voided",
        table.c.actor_id.in_(actor_ids),
    )


def figures(result) -> tuple:  # noqa: ANN001
    """Every field of a `VoidResult`, so two answers compare whole."""
    return (
        result.market_id,
        result.reason,
        result.voided_by,
        result.recorded_at,
        result.positions_refunded,
        result.total_owed,
        result.total_refunded,
        result.refunds_cut,
        result.residue,
    )
