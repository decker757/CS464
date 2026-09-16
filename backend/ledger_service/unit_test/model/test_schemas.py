"""The wire contract.

These generate /docs, which is what Michelle codes the balance display against.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

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
