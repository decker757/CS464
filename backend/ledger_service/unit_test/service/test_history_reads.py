"""How the trade history reads. [T-5] #25

Each structural test names the change that turns it red:

- **One statement.** The grant check's own statements are captured alone
  first and subtracted from the read's. Exactly one statement must be left,
  and it must read both `ledger.entries` and `ledger.transactions`. Reading the
  anchor sum in its own query beside the page leaves a second `entries`
  statement. Taking `kind` and `context` from
  `Entry.transaction`'s `selectin` load leaves a second `transactions` one.
  Reading a settlement row's winner from `ledger.market_results` leaves a
  second statement of its own.
- **One leg per USER account.** The mixed test stays green while every writer
  keeps one leg on the user. A writer that splits the user's side in two turns
  it red. The fabricated companion shows the guard's query can find a breach:
  weaken it to `HAVING count(*) > 2` and the companion goes red.
- **No call to market_service.** Any HTTP request from any
  `httpx.AsyncClient` fails the read and is recorded.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.history_fixtures import (
    read_history,
    traded,
    transaction_ids,
    transactions_with_two_legs_on_one_user_account,
)
from unit_test.portfolio_fixtures import funded, market_at
from unit_test.sell_fixtures import hold, sell
from unit_test.settlement_fixtures import PAID, WINNER, settled_market
from unit_test.trade_fixtures import (
    SMALL_QUANTITY,
    accounts_module,
    capture_sql,
    entities,
    grants,
    posting,
)


def _schema() -> str:
    from core.database import SCHEMA  # noqa: PLC0415

    return SCHEMA


def _without(statements: list[str], removed: list[str]) -> list[str]:
    """`statements` with each of `removed` taken out once."""
    left = list(statements)
    for statement in removed:
        assert statement in left, (
            f"the read no longer runs the grant check's statement:\n{statement}"
        )
        left.remove(statement)
    return left


# =========================================================================
# One statement per page
# =========================================================================
async def test_each_page_is_one_statement_after_the_grant_check(
    session: AsyncSession,
) -> None:
    """Page 1 and a cursor page, four rows at `limit=2`: the grant, the buy,
    the sell, and a payout from a settled market, newest, on page 1."""
    scenario = await traded(session)
    settled = await settled_market(session, [(scenario.user_id, WINNER, PAID)])
    schema = _schema()

    with capture_sql() as grant_check:
        await grants().ensure_granted(session, scenario.user_id)

    with capture_sql() as page_one_sql:
        page_one = await read_history(session, scenario.user_id, limit=2)
    with capture_sql() as page_two_sql:
        page_two = await read_history(
            session, scenario.user_id, limit=2, cursor=page_one.next_cursor
        )

    assert len(page_one.rows) == 2 and len(page_two.rows) == 2
    payout = page_one.rows[0]
    assert payout.entry.transaction.kind == entities().TransactionKind.SETTLEMENT
    assert payout.trade.outcome_id == settled.outcomes[1]
    for name, captured in (("page 1", page_one_sql), ("page 2", page_two_sql)):
        read = _without(captured, grant_check)
        assert len(read) == 1, f"{name} took {len(read)} statements:\n" + "\n\n".join(read)
        assert f"{schema}.entries" in read[0], f"{name} did not read entries:\n{read[0]}"
        assert f"{schema}.transactions" in read[0], (
            f"{name} did not read kind and context in the same statement:\n{read[0]}"
        )


# =========================================================================
# A transaction puts at most one leg on any USER account
# =========================================================================
async def test_no_transaction_puts_two_legs_on_a_user_account(
    session: AsyncSession,
) -> None:
    """Every writer there is: grants, buys and sells, two users, two markets,
    and the markets' seeds."""
    first = await traded(session)
    second_market = await market_at(session)
    other_id, _ = await funded(session)
    await hold(session, second_market, user_id=other_id)
    await hold(session, first.upstream, user_id=other_id, quantity=SMALL_QUANTITY, outcome=1)
    await sell(session, second_market, user_id=other_id)

    ents = entities()
    kinds = set(
        (await session.execute(select(ents.Transaction.kind).distinct())).scalars()
    )
    assert kinds == {
        ents.TransactionKind.SIGNUP_GRANT,
        ents.TransactionKind.MARKET_SEED,
        ents.TransactionKind.TRADE_BUY,
        ents.TransactionKind.TRADE_SELL,
    }
    assert await transactions_with_two_legs_on_one_user_account(session) == []


async def test_the_one_leg_guard_names_a_fabricated_two_leg_transaction(
    session: AsyncSession,
) -> None:
    """Fabricated on purpose: two legs on one user in one transaction, which
    `posting.post` accepts and no real writer produces. Without this, the
    test above could pass on a query that finds nothing."""
    user_id, _ = await funded(session)
    ents = entities()
    user = await accounts_module().ensure(session, ents.AccountKind.USER, user_id)
    platform = await accounts_module().ensure_platform(session)
    fabricated = await posting().post(
        session,
        idempotency_key=f"fabricated-two-legs:{uuid.uuid4()}",
        kind=ents.TransactionKind.SIGNUP_GRANT,
        legs=[
            posting().Leg(account=user, amount=Decimal("1.2345")),
            posting().Leg(account=user, amount=Decimal("2.3456")),
            posting().Leg(account=platform, amount=Decimal("-3.5801")),
        ],
    )

    assert await transactions_with_two_legs_on_one_user_account(session) == [
        fabricated.id
    ]


# =========================================================================
# Ids only
# =========================================================================
async def test_the_history_makes_no_call_to_market_service(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = await traded(session)
    scenario.upstream.calls = 0
    sent: list[str] = []

    async def refuse(self, request, *args, **kwargs):  # noqa: ANN001
        sent.append(str(request.url))
        raise AssertionError(f"the history called {request.url}")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)

    page = await read_history(session, scenario.user_id)

    assert sent == []
    assert scenario.upstream.calls == 0
    assert transaction_ids(page.rows)[:2] == [
        scenario.sell.transaction_id,
        scenario.buy.transaction_id,
    ]
    assert page.rows[0].trade.market_id == scenario.upstream.market_id
