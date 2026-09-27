"""`MarketClosed`, the one error [F-8] #109 adds. ADR 0017.

The shape the `LedgerError` handler keys on: base class, status and code.
"""

from __future__ import annotations


def _errors():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from core import errors  # noqa: PLC0415

    return errors


def test_market_closed_is_a_ledger_error() -> None:
    """The base class is what `register_error_handlers` dispatches on; outside
    it the error would surface as a 500."""
    assert issubclass(_errors().MarketClosed, _errors().LedgerError)


def test_market_closed_is_a_409() -> None:
    """409: the request is well formed and the state refuses it. ADR 0017."""
    assert _errors().MarketClosed.status_code == 409


def test_market_closed_spells_the_code_the_way_market_service_does() -> None:
    """`market_closed`, matching market_service's own code, so the frontend
    handles both with one handler. ADR 0017."""
    assert _errors().MarketClosed.code == "market_closed"
