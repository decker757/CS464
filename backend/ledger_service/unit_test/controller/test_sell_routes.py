"""A sell on the wire. [T-3] #23

What the controller owns for a sell: `side` accepting `"sell"`, the new
`409 insufficient_shares_held` with its details as decimal strings,
`422 proceeds_below_tick` in the envelope, the renamed OpenAPI operation, and
the error table in `docs/api/ledger-service.md` and in the declared responses.
Business rules are `unit_test/service/test_sell*.py`'s.

Built on `test_trade_routes.py`'s client, market stub and terms stub rather
than copies of them. Every holding is made by a real buy through this route,
on a book the first trade opens at zero — never `_warm`, which writes `q`.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import bearer
from unit_test.controller.test_trade_routes import (  # noqa: F401 - fixture
    _Market,
    _path,
    _Terms,
    trade_client,
)

_OPERATION = "execute_trade_ledger_markets__market_id__trades_post"
_TRADE_HEADING = "## POST /ledger/markets/{market_id}/trades"


def _body(market: _Market, *, side: str, quantity: str, version: int, key: str) -> dict:
    return {
        "outcome_id": str(market.outcomes[0]),
        "side": side,
        "quantity": quantity,
        "state_version": version,
        "idempotency_key": key,
    }


async def _bought(client, market: _Market, headers: dict, quantity: str = "54.3333"):
    """A real buy on a book this very trade opens at zero."""
    response = await client.post(
        _path(market.market_id),
        json=_body(market, side="buy", quantity=quantity, version=0, key="hold"),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _doc() -> str:
    return (Path(__file__).resolve().parents[4] / "docs/api/ledger-service.md").read_text(
        encoding="utf-8"
    )


def _section(doc: str, heading: str) -> str:
    return doc.split(heading, 1)[1].split("\n## ", 1)[0]


def _table_rows(section: str) -> set[tuple[str, str]]:
    """`(status, code)` for every body row of every table in `section`,
    backticks stripped. The header and the separator row are skipped."""
    rows = set()
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if cells[0] in ("Status", "") or set(cells[0]) <= set("-: "):
            continue
        rows.add((cells[0], cells[1]))
    return rows


def _prose(section: str) -> list[str]:
    """The paragraphs of `section` that are not a table."""
    return [
        p
        for p in re.split(r"\n\s*\n", section)
        if p.strip() and not p.lstrip().startswith("|")
    ]


# =========================================================================
# The side
# =========================================================================
async def test_the_route_accepts_a_sell_and_the_balance_rises(
    trade_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replaces #22's `test_the_route_refuses_a_sell`. A sell is executed as a
    sell: `201`, `side: "sell"`, a positive `total`, and the balance rises by
    it."""
    client, recorder = trade_client
    market = _Market()
    _Terms().install(monkeypatch, market)
    headers = bearer(uuid.uuid4())
    await _bought(client, market, headers)
    before = Decimal((await client.get("/ledger/balances/me", headers=headers)).json()["balance"])

    response = await client.post(
        _path(market.market_id),
        json=_body(market, side="sell", quantity="10.0000", version=1, key="sell"),
        headers=headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["side"] == "sell"
    assert body["total"] == "5.8905"
    after = Decimal((await client.get("/ledger/balances/me", headers=headers)).json()["balance"])
    assert after == before + Decimal("5.8905")
    assert len(recorder.calls) == 2


@pytest.mark.parametrize("side", ["short", "Sell", "BUY", ""])
async def test_a_side_other_than_buy_or_sell_is_422(
    trade_client, monkeypatch: pytest.MonkeyPatch, side: str
) -> None:
    """FastAPI's own validation, so there is no `code` to assert. The
    refusal is pinned by its location instead — `body.side` — so a 422 for
    some other field cannot pass this. That is a narrower pin than
    `test_trade_routes.py`'s convention of asserting the status only, and
    the only one available for a validation refusal."""
    client, recorder = trade_client
    market = _Market()
    _Terms().install(monkeypatch, market)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, side=side, quantity="10.0000", version=0, key="k"),
        headers=bearer(uuid.uuid4()),
    )

    assert response.status_code == 422
    assert [e["loc"] for e in response.json()["detail"]] == [["body", "side"]]
    assert recorder.calls == []


# =========================================================================
# The new refusals
# =========================================================================
async def test_insufficient_shares_held_is_409_with_decimal_string_details(
    trade_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves of the criterion: a caller with no position is told
    `held: "0.0000"`, and a holder asking for more is told what they hold.
    `requested` is normalised to scale 4 — `"10"` comes back `"10.0000"`."""
    client, recorder = trade_client
    market = _Market()
    _Terms().install(monkeypatch, market)
    holder = bearer(uuid.uuid4())
    await _bought(client, market, holder)
    published = len(recorder.calls)

    stranger = await client.post(
        _path(market.market_id),
        json=_body(market, side="sell", quantity="10", version=1, key="s"),
        headers=bearer(uuid.uuid4()),
    )
    over = await client.post(
        _path(market.market_id),
        json=_body(market, side="sell", quantity="54.3334", version=1, key="o"),
        headers=holder,
    )

    for response in (stranger, over):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "insufficient_shares_held"
    assert stranger.json()["error"]["details"] == {
        "held": "0.0000",
        "requested": "10.0000",
    }
    assert over.json()["error"]["details"] == {
        "held": "54.3333",
        "requested": "54.3334",
    }
    assert len(recorder.calls) == published


async def test_proceeds_below_tick_is_422_with_its_code(
    trade_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One tick of `13.3333` held shares: raw proceeds `0.0000524…`, floored
    to nothing. In the envelope, with the code, publishing nothing."""
    client, recorder = trade_client
    market = _Market()
    _Terms().install(monkeypatch, market)
    headers = bearer(uuid.uuid4())
    await _bought(client, market, headers, quantity="13.3333")
    published = len(recorder.calls)

    response = await client.post(
        _path(market.market_id),
        json=_body(market, side="sell", quantity="0.0001", version=1, key="tick"),
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "proceeds_below_tick"
    assert len(recorder.calls) == published


# =========================================================================
# The contract at /docs
# =========================================================================
async def _operation(client) -> dict:
    return (await client.get("/openapi.json")).json()["paths"][
        "/ledger/markets/{market_id}/trades"
    ]["post"]


async def test_the_operation_is_execute_trade_and_no_longer_says_buy_only(
    trade_client,
) -> None:
    """A contract change the frontend is told about. The operationId is the
    one FastAPI generates from the handler's name — no explicit
    `operation_id`."""
    client, _ = trade_client
    operation = await _operation(client)

    assert operation["operationId"] == _OPERATION
    assert not operation["summary"].lower().startswith("buy")
    assert "Buy only" not in operation["description"]
    assert "refused `422` until [T-3]" not in operation["description"]


async def test_the_declared_responses_are_the_tables_and_omit_the_unreachable_two(
    trade_client,
) -> None:
    client, _ = trade_client
    operation = await _operation(client)
    responses = operation["responses"]

    assert set(responses) == {"201", "401", "404", "409", "422", "500", "503"}
    assert "insufficient_shares_held" in responses["409"]["description"]
    assert "proceeds_below_tick" in responses["422"]["description"]

    serialised = json.dumps(operation).lower()
    assert "insufficient_shares_outstanding" not in serialised
    assert "market_not_published" not in serialised


def test_the_docs_error_table_is_exactly_the_issues() -> None:
    """Every row, and no other — including 401, which the route declares and
    `test_a_trade_needs_a_token` holds."""
    rows = _table_rows(_section(_doc(), _TRADE_HEADING))

    assert rows == {
        ("401", "invalid_token"),
        ("404", "market_not_found"),
        ("409", "market_closed"),
        ("409", "quote_stale"),
        ("409", "insufficient_funds"),
        ("409", "insufficient_shares_held"),
        ("409", "idempotency_key_reused"),
        ("422", "unknown_outcome"),
        ("422", "quantity_too_large"),
        ("422", "cost_below_tick"),
        ("422", "proceeds_below_tick"),
        ("422", "—"),
        ("500", "market_book_incomplete"),
        ("503", "market_terms_unavailable"),
    }


def test_the_docs_state_the_preview_holdings_asymmetry() -> None:
    """One sentence in the trade section's prose: a preview can quote a sell
    the trade then refuses `insufficient_shares_held`."""
    paragraphs = _prose(_section(_doc(), _TRADE_HEADING))

    assert any(
        "preview" in p.lower() and "insufficient_shares_held" in p for p in paragraphs
    )


def test_the_errors_prose_says_the_trade_route_cannot_return_outstanding() -> None:
    """Out of the table, into the prose, as `unbalanced_transaction` is: the
    `## Errors` section names `insufficient_shares_outstanding` in the same
    paragraph as the trade route."""
    paragraphs = _prose(_section(_doc(), "\n## Errors"))

    assert any(
        "insufficient_shares_outstanding" in p and "trade route" in p
        for p in paragraphs
    )
