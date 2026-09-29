"""The trade history on the wire. [T-5] #25

What the controller owns: both routes return the same body, every field [4.1]
#13 returned is still there and unchanged, the five trade fields are present
(null where there is no trade), and every amount, quantity and price is a
decimal string at scale 4. The figures are `test_trade_history.py`'s: #23's
worked example, reached through real trades.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from unit_test.conftest import bearer
from unit_test.controller.test_sell_routes import _doc, _prose, _section
from unit_test.history_fixtures import (
    BUY_AVERAGE,
    BUY_COST,
    EXISTING_FIELDS,
    SELL_AVERAGE,
    TRADE_FIELDS,
    stored_context,
    traded,
)
from unit_test.sell_fixtures import PROCEEDS
from unit_test.trade_fixtures import QUANTUM, entities

MY_ENTRIES = "/ledger/entries/me"
_HEADING = "## GET /ledger/entries/me"
_SCALE_FOUR = re.compile(r"^-?\d+\.\d{4}$")
_ON_SCREEN_GUARANTEE = "strictly newer than every row already on screen"


def _admin_entries(user_id: uuid.UUID) -> str:
    return f"/ledger/users/{user_id}/entries"


def _scale_four(value: Decimal) -> str:
    return str(value.quantize(QUANTUM))


async def _entry_id(
    session: AsyncSession, user_id: uuid.UUID, transaction_id: uuid.UUID
) -> uuid.UUID:
    """The id of this transaction's leg on the user's account."""
    ents = entities()
    return (
        await session.execute(
            select(ents.Entry.id)
            .join(ents.Account, ents.Account.id == ents.Entry.account_id)
            .where(
                ents.Account.kind == ents.AccountKind.USER,
                ents.Account.owner_id == user_id,
                ents.Entry.transaction_id == transaction_id,
            )
        )
    ).scalar_one()


async def _my_entries(client: AsyncClient, user_id: uuid.UUID) -> list[dict]:
    response = await client.get(MY_ENTRIES, headers=bearer(user_id, UserRole.TRADER))
    assert response.status_code == 200, response.text
    return response.json()["entries"]


# =========================================================================
# Both routes, one body
# =========================================================================
async def test_the_admin_route_returns_the_same_body_as_the_user_s_own(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    scenario = await traded(session)

    own = await client.get(MY_ENTRIES, headers=bearer(scenario.user_id, UserRole.TRADER))
    admin = await client.get(_admin_entries(scenario.user_id), headers=admin_headers)

    assert own.status_code == admin.status_code == 200, (own.text, admin.text)
    assert admin.json() == own.json()
    sell_row, buy_row, _ = admin.json()["entries"]
    for row in (sell_row, buy_row):
        assert row["market_id"] == str(scenario.upstream.market_id)
        assert row["average_price"] is not None


# =========================================================================
# The fields
# =========================================================================
async def test_existing_fields_are_present_and_unchanged(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Nothing [4.1] #13 returned is removed or renamed, `context` included,
    and `context` is what the trade stored: the sell's still says `"10"`,
    though its typed `quantity` says `"10.0000"`."""
    scenario = await traded(session)

    entries = await _my_entries(client, scenario.user_id)

    for entry in entries:
        assert set(entry) == EXISTING_FIELDS | set(TRADE_FIELDS)
    sell_row, buy_row, grant_row = entries
    for row, transaction_id, kind in (
        (sell_row, scenario.sell.transaction_id, "trade_sell"),
        (buy_row, scenario.buy.transaction_id, "trade_buy"),
        (grant_row, scenario.grant_id, "signup_grant"),
    ):
        assert row["id"] == str(await _entry_id(session, scenario.user_id, transaction_id))
        assert row["transaction_id"] == str(transaction_id)
        assert row["kind"] == kind
        assert row["context"] == await stored_context(session, transaction_id)
        assert row["created_at"].endswith(("Z", "+00:00"))
    assert sell_row["context"]["quantity"] == "10"


async def test_amounts_quantities_and_prices_are_scale_four_strings(
    client: AsyncClient, session: AsyncSession
) -> None:
    scenario = await traded(session)

    sell_row, buy_row, grant_row = await _my_entries(client, scenario.user_id)

    assert {k: sell_row[k] for k in ("amount", "balance_after", *TRADE_FIELDS)} == {
        "amount": str(PROCEEDS),
        "balance_after": _scale_four(scenario.after_sell),
        "market_id": str(scenario.upstream.market_id),
        "outcome_id": str(scenario.upstream.outcomes[0]),
        "side": "sell",
        "quantity": "10.0000",
        "average_price": str(SELL_AVERAGE),
    }
    assert {k: buy_row[k] for k in ("amount", "balance_after", *TRADE_FIELDS)} == {
        "amount": str(-BUY_COST),
        "balance_after": _scale_four(scenario.after_buy),
        "market_id": str(scenario.upstream.market_id),
        "outcome_id": str(scenario.upstream.outcomes[0]),
        "side": "buy",
        "quantity": "54.3333",
        "average_price": str(BUY_AVERAGE),
    }
    assert grant_row["amount"] == grant_row["balance_after"] == _scale_four(scenario.credits)
    assert {k: grant_row[k] for k in TRADE_FIELDS} == dict.fromkeys(TRADE_FIELDS)

    for row in (sell_row, buy_row, grant_row):
        for key in ("amount", "balance_after", "quantity", "average_price"):
            if row[key] is not None:
                assert _SCALE_FOUR.match(row[key]), f"{key} = {row[key]!r}"


# =========================================================================
# The docs
# =========================================================================
def test_the_docs_document_the_new_fields_and_promise_rows_on_screen_do_not_move() -> None:
    doc = _doc()
    section = _section(doc, _HEADING)
    example = section.split("```jsonc", 1)[1].split("```", 1)[0]

    for field in TRADE_FIELDS:
        assert f'"{field}"' in example, f"the example does not show `{field}`"
    says_when_null = [
        p for p in _prose(section) if "null" in p and "average_price" in p
    ]
    assert says_when_null, "the section never says when the trade fields are null"

    assert "Today the only value is `signup_grant`" not in section
    for kind in ("signup_grant", "trade_buy", "trade_sell"):
        assert f"`{kind}`" in section, f"the kinds list does not name `{kind}`"

    # #187 restored the guarantee #25 had to withdraw.
    # Joined, so a re-wrap of the paragraph does not read as a broken promise.
    assert _ON_SCREEN_GUARANTEE in " ".join(section.split())
    assert "Known limitation" not in section
