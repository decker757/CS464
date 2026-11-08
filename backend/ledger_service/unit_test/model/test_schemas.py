"""The wire contract.

These generate /docs, which is what Michelle codes the balance display against.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.pricing import Side
from model.entities import TransactionKind
from model.schemas import BalanceOut, FillOut, LedgerEntryOut, OrderOut


def _entry(**overrides: object) -> LedgerEntryOut:
    base = {
        "id": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "amount": Decimal("1000.0000"),
        "balance_after": Decimal("1000.0000"),
        "transaction_id": uuid.uuid4(),
        "kind": TransactionKind.SIGNUP_GRANT,
        "context": None,
    }
    return LedgerEntryOut(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_balance_serialises_as_a_string() -> None:
    """Exactness is kept on the wire, not just in the column. A JSON number is
    an IEEE double by the time a browser has parsed it."""
    out = BalanceOut(
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        balance=Decimal("1000.0000"),
        reserved=Decimal("0.0000"),
        available=Decimal("1000.0000"),
    )

    assert out.model_dump()["balance"] == "1000.0000"


def test_a_balance_keeps_its_scale() -> None:
    """`1000` and `1000.0000` are the same number and different strings, and
    the frontend formats one of them. It should always get the same one."""
    out = BalanceOut(
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        balance=Decimal("0.5000"),
        reserved=Decimal("0.0000"),
        available=Decimal("0.5000"),
    )

    assert out.model_dump()["balance"] == "0.5000"


def test_reserved_and_available_serialise_as_strings_too() -> None:
    """The module's rule holds for every amount on the balance, not only the
    first: a python-mode dump gives strings, as the wire does (ADR 0024)."""
    out = BalanceOut(
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        balance=Decimal("100.0000"),
        reserved=Decimal("60.0000"),
        available=Decimal("40.0000"),
    )

    dumped = out.model_dump()
    assert (dumped["reserved"], dumped["available"]) == ("60.0000", "40.0000")


def test_an_entry_amount_serialises_as_a_string() -> None:
    assert _entry(amount=Decimal("-250.0000")).model_dump()["amount"] == "-250.0000"


def test_an_entry_flattens_its_transaction() -> None:
    """Nobody reading their own history wants the nesting. They want a
    statement: a date, an amount, and what it was for."""

    class _Fake:
        id = uuid.uuid4()
        created_at = datetime.now(UTC)
        amount = Decimal("1000.0000")
        transaction_id = uuid.uuid4()

        class transaction:  # noqa: N801
            kind = TransactionKind.SIGNUP_GRANT
            context = {"user_id": "abc"}

    out = LedgerEntryOut.of(_Fake(), balance_after=Decimal("1000.0000"))

    assert out.kind is TransactionKind.SIGNUP_GRANT
    assert out.context == {"user_id": "abc"}


def test_a_running_balance_serialises_as_a_string() -> None:
    """Same rule as `amount`, and for the same reason: this is money, and a
    JSON number is an IEEE double by the time a browser has parsed it."""
    out = _entry(balance_after=Decimal("750.0000"))

    assert out.model_dump()["balance_after"] == "750.0000"


# --- OrderOut and FillOut: the limit-order responses --------------------------
_ORDER_AMOUNTS = (
    "limit_price",
    "quantity",
    "filled_quantity",
    "average_fill_price",
    "reserved_credits",
    "reserved_shares",
)


def _order(side: Side, **amounts: Decimal | None) -> OrderOut:
    return OrderOut(
        id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        outcome_id=uuid.uuid4(),
        outcome_position=0,
        side=side,
        status="open",
        created_at=datetime.now(UTC),
        cancelled_at=None,
        **amounts,
    )


def test_an_orders_amounts_serialise_as_strings_in_python_mode_too() -> None:
    """The module's rule for every amount on the order object, as
    `BalanceOut` keeps it: a python-mode dump gives the wire's strings, and a
    null stays null (ADR 0024). The contract's buy, part filled, and its
    sell."""
    buy = _order(
        Side.BUY,
        limit_price=Decimal("0.6000"),
        quantity=Decimal("100.0000"),
        filled_quantity=Decimal("20.5465"),
        average_fill_price=Decimal("0.5750"),
        reserved_credits=Decimal("48.1848"),
        reserved_shares=None,
    )
    sell = _order(
        Side.SELL,
        limit_price=Decimal("0.6000"),
        quantity=Decimal("30.0000"),
        filled_quantity=Decimal("13.7867"),
        average_fill_price=Decimal("0.6164"),
        reserved_credits=None,
        reserved_shares=Decimal("16.2133"),
    )

    dumped = buy.model_dump()
    assert [dumped[field] for field in _ORDER_AMOUNTS] == [
        "0.6000",
        "100.0000",
        "20.5465",
        "0.5750",
        "48.1848",
        None,
    ]
    assert sell.model_dump()["reserved_shares"] == "16.2133"


def test_a_fills_amounts_serialise_as_strings_in_python_mode_too() -> None:
    fill = FillOut(
        transaction_id=uuid.uuid4(),
        quantity=Decimal("20.5465"),
        total=Decimal("-11.8152"),
        state_version=1,
    )

    dumped = fill.model_dump()
    assert (dumped["quantity"], dumped["total"]) == ("20.5465", "-11.8152")


# --- PreviewOut and OutcomePriceOut: the contract /docs shows ----------------
def _example(model: type, field: str) -> Decimal:
    return Decimal(model.model_fields[field].examples[0])


def test_the_preview_examples_are_a_trade_that_could_happen() -> None:
    """`average_price` is `abs(total) / quantity`, at most 1, above the price.

    The examples are what `/docs` shows the frontend, so they must be a
    consistent buy: the average above the price the buy started from.
    """
    from model.schemas import OutcomePriceOut, PreviewOut  # noqa: PLC0415

    quantity = _example(PreviewOut, "quantity")
    total = _example(PreviewOut, "total")
    average = _example(PreviewOut, "average_price")
    price = _example(OutcomePriceOut, "price")

    assert (total.copy_abs() / quantity).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    ) == average
    # `<=`: a sub-tick buy is charged a whole tick (D-039) and can average
    # exactly 1.0000.
    assert Decimal(0) < average <= Decimal(1)
    assert total < 0, "the example is meant to be a buy"
    assert average > price, "a buy averages above the price it started from"


@pytest.mark.parametrize("price", ["-0.0001", "1.0001"])
def test_an_outcome_price_outside_zero_to_one_is_refused(price: str) -> None:
    """The bounds `realtime_service`'s `OutcomePrice` puts on the same field:
    a pricing bug producing `1.0001` is refused there, so it must be here.
    """
    from model.schemas import OutcomePriceOut  # noqa: PLC0415

    with pytest.raises(ValidationError):
        OutcomePriceOut(outcome_id=uuid.uuid4(), position=0, price=Decimal(price))


@pytest.mark.parametrize("price", ["0", "0.6234", "1"])
def test_an_outcome_price_at_or_inside_the_bounds_is_accepted(price: str) -> None:
    """Both ends inclusive, as on the socket: a saturated outcome rounds to them."""
    from model.schemas import OutcomePriceOut  # noqa: PLC0415

    assert OutcomePriceOut(
        outcome_id=uuid.uuid4(), position=0, price=Decimal(price)
    ).price == Decimal(price)


def test_the_documented_preview_example_is_a_trade_that_could_happen() -> None:
    """The same arithmetic on `docs/api/ledger-service.md`'s example, which the
    frontend reads as often as `/docs`. It carries its prices, so it is also
    regenerated from the engine and compared.
    """
    doc = (Path(__file__).resolve().parents[4] / "docs/api/ledger-service.md").read_text(
        encoding="utf-8"
    )
    section = doc.split("## GET /ledger/markets/{market_id}/preview", 1)[1]
    example = section.split("```jsonc", 1)[1].split("```", 1)[0]

    def field(name: str) -> Decimal:
        match = re.search(rf'"{name}":\s*"([^"]+)"', example)
        assert match, f"{name} missing from the documented example"
        return Decimal(match.group(1))

    quantity, total, average = field("quantity"), field("total"), field("average_price")

    assert (total.copy_abs() / quantity).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    ) == average
    # `<=`: a sub-tick buy is charged a whole tick (D-039) and can average
    # exactly 1.0000.
    assert Decimal(0) < average <= Decimal(1)

    def listed(name: str) -> list[Decimal]:
        block = example.split(f'"{name}"', 1)[1].split("]", 1)[0]
        return [Decimal(v) for v in re.findall(r'"price":\s*"([^"]+)"', block)]

    before, after = listed("prices"), listed("post_trade_prices")
    for side in (before, after):
        assert abs(sum(side) - Decimal(1)) <= Decimal("0.0001") * len(side)

    assert total < 0, "the example is meant to be a buy of position 0"
    assert before[0] < average < after[0], (
        f"a buy of position 0 averages between {before[0]} and {after[0]}; "
        f"got {average}"
    )

    # The checks above hold for any consistent set of figures, so regenerate
    # the example from its book and compare.
    from core.lmsr import cost_to_trade, prices  # noqa: PLC0415
    from core.pricing import (  # noqa: PLC0415
        Side,
        quantize_cost,
        quantize_price,
    )

    q = [Decimal("137.5000"), Decimal("42.2500")]
    b = Decimal("100")
    delta = [quantity, Decimal(0)]
    magnitude = quantize_cost(
        cost_to_trade(q, b, delta).copy_abs(), side=Side.BUY
    )

    assert -magnitude == total, f"the documented total is not {-magnitude}"
    assert quantize_price(magnitude / quantity) == average
    assert [quantize_price(p) for p in prices(q, b)] == before
    assert [
        quantize_price(p) for p in prices([q[0] + quantity, q[1]], b)
    ] == after


# --- TradeIn: a limit for its side, or a quote. #640 ---------------------------
def _trade(**fields: object) -> dict[str, object]:
    return {
        "outcome_id": str(uuid.uuid4()),
        "quantity": "10",
        "idempotency_key": "k-1",
        **fields,
    }


def _refusal_type_of(body: dict[str, object]) -> str:
    from model.schemas import TradeIn  # noqa: PLC0415

    with pytest.raises(ValidationError) as refused:
        TradeIn.model_validate(body)
    (error,) = refused.value.errors()
    return error["type"]


def test_a_buy_with_a_max_cost_needs_no_state_version() -> None:
    from model.schemas import TradeIn  # noqa: PLC0415

    trade = TradeIn.model_validate(_trade(side="buy", max_cost="6.5000"))

    assert (trade.max_cost, trade.state_version) == (Decimal("6.5000"), None)


@pytest.mark.parametrize("min_proceeds", ["0", "4.5"])
def test_a_sell_with_a_min_proceeds_needs_no_state_version(min_proceeds: str) -> None:
    """`0` too: a sell that accepts any price still names its limit, and a
    truthiness check would mistake it for no limit at all.
    """
    from model.schemas import TradeIn  # noqa: PLC0415

    trade = TradeIn.model_validate(_trade(side="sell", min_proceeds=min_proceeds))

    assert (trade.min_proceeds, trade.state_version) == (Decimal(min_proceeds), None)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_a_trade_with_no_limit_still_requires_a_state_version(side: str) -> None:
    """The expand release's promise: a body from today's frontend, which
    names no limit, keeps today's rule until that frontend is changed.
    """
    from model.schemas import TradeIn  # noqa: PLC0415

    assert _refusal_type_of(_trade(side=side)) == "value_error"
    assert TradeIn.model_validate(_trade(side=side, state_version=7)).state_version == 7


def test_a_min_proceeds_on_a_buy_is_refused() -> None:
    # `state_version` is sent, so only the side rule can refuse it.
    body = _trade(side="buy", min_proceeds="4.5", state_version=7)

    assert _refusal_type_of(body) == "value_error"


def test_a_max_cost_on_a_sell_is_refused() -> None:
    body = _trade(side="sell", max_cost="6.5", state_version=7)

    assert _refusal_type_of(body) == "value_error"


@pytest.mark.parametrize(
    ("side", "field"), [("buy", "max_cost"), ("sell", "min_proceeds")]
)
def test_a_limit_at_five_decimal_places_is_refused(side: str, field: str) -> None:
    """Four places, as `quantity` (D-038): a fifth is 422, not rounded."""
    body = _trade(side=side, **{field: "6.50001"})

    assert _refusal_type_of(body) == "decimal_max_places"


@pytest.mark.parametrize(
    ("side", "fields", "limit"),
    [
        ("buy", {"max_cost": "6.5"}, Decimal("6.5")),
        ("sell", {"min_proceeds": "0"}, Decimal("0")),
        ("buy", {"state_version": 7}, None),
    ],
)
def test_the_limit_property_picks_the_field_for_the_side(
    side: str, fields: dict[str, object], limit: Decimal | None
) -> None:
    from model.schemas import TradeIn  # noqa: PLC0415

    assert TradeIn.model_validate(_trade(side=side, **fields)).limit == limit
