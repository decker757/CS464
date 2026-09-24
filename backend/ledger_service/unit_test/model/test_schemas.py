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

from model.entities import TransactionKind
from model.schemas import BalanceOut, LedgerEntryListResponse, LedgerEntryOut


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
        user_id=uuid.uuid4(), account_id=uuid.uuid4(), balance=Decimal("1000.0000")
    )

    assert out.model_dump()["balance"] == "1000.0000"


def test_a_balance_keeps_its_scale() -> None:
    """`1000` and `1000.0000` are the same number and different strings, and
    the frontend formats one of them. It should always get the same one."""
    out = BalanceOut(
        user_id=uuid.uuid4(), account_id=uuid.uuid4(), balance=Decimal("0.5000")
    )

    assert out.model_dump()["balance"] == "0.5000"


def test_an_entry_amount_serialises_as_a_string() -> None:
    assert _entry(amount=Decimal("-250.0000")).model_dump()["amount"] == "-250.0000"


def test_a_negative_amount_keeps_its_sign() -> None:
    """Signed on the wire, as in the column: negative took credits out of this
    account. A client should not have to infer direction from `kind`."""
    assert _entry(amount=Decimal("-250.0000")).amount < 0


def test_a_naive_timestamp_is_given_an_offset() -> None:
    """A driver handing back a naive datetime would make one entry serialise
    with a trailing Z and another without, leaving the frontend to special-case
    which."""
    out = _entry(created_at=datetime(2026, 9, 15, 12, 0, 0))

    assert out.created_at.tzinfo is not None


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


def test_has_more_and_next_cursor_agree() -> None:
    """Stated separately so a client can drive a 'load more' control without
    reasoning about the cursor at all."""
    page = LedgerEntryListResponse(entries=[], next_cursor="abc", has_more=True)

    assert page.has_more is True
    assert page.next_cursor == "abc"


# --- PreviewOut and OutcomePriceOut: the contract /docs shows ----------------
def _example(model: type, field: str) -> Decimal:
    return Decimal(model.model_fields[field].examples[0])


def test_the_preview_examples_are_a_trade_that_could_happen() -> None:
    """`average_price` is `abs(total) / quantity`, under 1, and above the price.

    The examples are what `/docs` shows the frontend. LMSR prices sum to 1
    and each is in (0, 1), so an average price per share of 1 or more cannot
    occur, and an example that is not the quotient its own description
    defines teaches the wrong formula. The total is negative, so it is a
    buy, and a buy pushes the price up as it fills: its average is strictly
    above the price it started from, which is `OutcomePriceOut`'s example.
    """
    from model.schemas import OutcomePriceOut, PreviewOut  # noqa: PLC0415

    quantity = _example(PreviewOut, "quantity")
    total = _example(PreviewOut, "total")
    average = _example(PreviewOut, "average_price")
    price = _example(OutcomePriceOut, "price")

    assert (total.copy_abs() / quantity).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    ) == average
    # `<=`, not `<`. The smallest buy there is costs a fraction of a tick
    # and is charged the whole one (D-039), which divides out to exactly
    # 1.0000 in an ordinary market — so an average of 1 is a real quote,
    # not a bad example. D-044 has the skewed case that reads higher.
    assert Decimal(0) < average <= Decimal(1)
    assert total < 0, "the example is meant to be a buy"
    assert average > price, "a buy averages above the price it started from"


@pytest.mark.parametrize("price", ["-0.0001", "1.0001"])
def test_an_outcome_price_outside_zero_to_one_is_refused(price: str) -> None:
    """The bounds `realtime_service`'s `OutcomePrice` puts on the same field.

    Field-for-field parity is the promise that lets one client renderer
    serve the snapshot, the price frame and this preview, and `price` is the
    one field carrying an invariant. A pricing bug producing `1.0001` is
    refused on the socket; without these it ships here.
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
    """The same arithmetic on `docs/api/ledger-service.md`'s example.

    Two copies of one contract, and the review found both wrong, so both are
    checked. The frontend reads the markdown as often as it reads `/docs`.
    That example carries its prices, so it is held to the stronger rule: on
    a buy, the average price sits strictly between the traded outcome's price
    before and after, and each price list sums to 1 within rounding.
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
    # `<=`, not `<`. The smallest buy there is costs a fraction of a tick
    # and is charged the whole one (D-039), which divides out to exactly
    # 1.0000 in an ordinary market — so an average of 1 is a real quote,
    # not a bad example. D-044 has the skewed case that reads higher.
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

    # And the figures are a trade the engine actually produces, which the
    # checks above cannot show. They hold for any internally consistent set:
    # rewrite `post_trade_prices` to 0.9900 / 0.0100 and every one of them
    # still passes, though those endpoints imply a cost near 9.13 rather than
    # the documented 7.32. The example is generated from this book, so the
    # test regenerates it and compares.
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
