"""Setup the preview and snapshot tests share. [T-1] #21, [F-9] #112

Both read the same book through `service/book_prices.py` (D-052), so their
tests need the same stand-in market service, the same warm book and the same
statement capture. The service tests and the route tests use the same pieces.

Project modules are reached through functions, not imported at module scope,
so a missing name fails one test rather than collection (D-007).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import httpx
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import mint_token, terms_client_over

QUANTUM = Decimal("0.0001")
B = Decimal("100.0000")
SUBSIDY = Decimal("250.0000")

# Asymmetric on purpose: with a uniform `q`, reading the vector in the wrong
# order would be invisible.
Q = [Decimal("137.5000"), Decimal("42.2500")]


# --- lazy module handles --------------------------------------------------
def books():
    from service import books  # noqa: PLC0415

    return books


def entities():
    from model import entities  # noqa: PLC0415

    return entities


def errors():
    from core import errors  # noqa: PLC0415

    return errors


# --- the market service ---------------------------------------------------
class Upstream:
    """A stand-in market service that counts its calls (D-008's one-off
    cost) and keeps the forwarded token (D-018)."""

    def __init__(
        self,
        *,
        published_at: str | None = "2026-09-01T09:00:00Z",
        status: str = "open",
        outcomes: int = 2,
    ) -> None:
        self.calls = 0
        self.tokens: list[str] = []
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4() for _ in range(outcomes)]
        self.body = {
            "id": str(self.market_id),
            "status": status,
            "close_time": "2027-01-05T12:00:00Z",
            "resolution_time": "2027-01-20T12:00:00Z",
            "liquidity_b": str(B),
            "seed_subsidy": str(SUBSIDY),
            "published_at": published_at,
            "outcomes": [
                {"id": str(o), "position": i, "label": f"Outcome {i}"}
                for i, o in enumerate(self.outcomes)
            ],
        }

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            header = request.headers.get("Authorization", "")
            self.tokens.append(header.removeprefix("Bearer "))
            return httpx.Response(200, json=self.body)

        return httpx.MockTransport(handler)

    @property
    def dead(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            raise httpx.ConnectError("market service is down")

        return httpx.MockTransport(handler)

    @property
    def missing(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return httpx.Response(404, json={"error": {"code": "market_not_found"}})

        return httpx.MockTransport(handler)


class TermsStub:
    """A stub for `market_terms.fetch`, for route tests, which cannot hand a
    route a transport. Records what it was asked.

    Patched onto the module, which `books.py` resolves at call time.
    """

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls = 0
        self.tokens: list[str] = []
        self.market_id = uuid.uuid4()
        self.outcomes = [uuid.uuid4(), uuid.uuid4()]
        self._raises = raises

    def install(self, monkeypatch: pytest.MonkeyPatch, *, published: bool = True):
        from service import market_terms  # noqa: PLC0415

        if published:
            published_at = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
        else:
            published_at = None

        async def fake(market_id, *, access_token, terms_client=None):  # noqa: ANN001
            self.calls += 1
            self.tokens.append(access_token)
            if self._raises is not None:
                raise self._raises
            return market_terms.MarketTerms(
                market_id=market_id,
                # Explicit: `MarketTerms.status` has no default, because a
                # default of "open" is a fail-open on the trade gate.
                status="open",
                liquidity_b=B,
                seed_subsidy=SUBSIDY,
                published_at=published_at,
                outcomes=[
                    market_terms.OutcomeTerms(outcome_id=o, position=i)
                    for i, o in enumerate(self.outcomes)
                ],
            )

        monkeypatch.setattr(market_terms, "fetch", fake)
        return self


def fresh_token() -> str:
    """A valid trader's token, for a caller nobody asserts about."""
    return mint_token(uuid.uuid4())


# --- the book -------------------------------------------------------------
async def set_q(
    session: AsyncSession, upstream: Upstream, q: Sequence[Decimal]
) -> None:
    """Write `q` directly, as a trade would have left it. Committed."""
    outcome = entities().MarketOutcome
    for position, value in enumerate(q):
        await session.execute(
            update(outcome)
            .where(
                outcome.market_id == upstream.market_id,
                outcome.position == position,
            )
            .values(q=value)
        )
    await session.commit()


async def warm(
    session: AsyncSession, upstream: Upstream, q: Sequence[Decimal] = tuple(Q)
):
    """A market whose book exists, committed, and whose outcomes hold `q`.

    The upstream's call count and tokens are reset afterwards, so a test
    counts only the calls it makes itself.
    """
    book = await books().ensure_open(
        session,
        upstream.market_id,
        access_token=fresh_token(),
        terms_client=terms_client_over(upstream.transport),
    )
    await set_q(session, upstream, q)
    upstream.calls = 0
    upstream.tokens.clear()
    return book


async def book_row(session: AsyncSession, upstream: Upstream):
    """The book's row as the database holds it now."""
    book = entities().MarketBook
    session.expire_all()
    return (
        await session.execute(select(book).where(book.market_id == upstream.market_id))
    ).scalar_one()


def expected_prices(q: Sequence[Decimal]) -> list[Decimal]:
    """Recomputed from `core/lmsr.py` rather than pinned, rounded half up:
    nobody is charged a price."""
    from core.lmsr import prices  # noqa: PLC0415

    return [p.quantize(QUANTUM, rounding=ROUND_HALF_UP) for p in prices(list(q), B)]


# --- the SQL a call sends -------------------------------------------------
@contextmanager
def capture_sql() -> Iterator[list[str]]:
    """Every statement the block sends to Postgres, in order, from the
    engine's `before_cursor_execute`."""
    from core.database import get_engine  # noqa: PLC0415

    statements: list[str] = []
    engine = get_engine().sync_engine

    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


def mentioning(statements: Sequence[str], table: str) -> list[str]:
    return [s for s in statements if table in s.lower()]


def writes(statements: Sequence[str]) -> list[str]:
    return [
        s
        for s in statements
        if s.strip().lower().startswith(("insert", "update", "delete"))
    ]
