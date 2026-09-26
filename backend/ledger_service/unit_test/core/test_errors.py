"""`MarketClosed`, the one error [F-8] #109 adds. ADR 0017.

No database and no HTTP. `core/errors.py` is plain exceptions with no
framework imports — that is the whole reason it sits below `service/` — so
what can be asserted here is the shape the handler keys on, and nothing more.

**Why there is no route test beside this one.** `controller/errors.py`
registers a single handler on `LedgerError` and reads `status_code` and `code`
off the instance, so any subclass is mapped by construction. The envelope is
already pinned end to end by `test_preview_routes.py`, which drives
`MarketTermsUnavailable` through a real route and asserts the 503 and the
code. `MarketClosed` has no route that can raise it until [T-2] #22 builds the
trade endpoint, and standing up a fake one here would be asserting against a
stub — so the route-level assertion is #22's, and what this file holds is the
three facts that make the mapping work when it arrives.
"""

from __future__ import annotations


def _errors():
    """Imported inside each test rather than at module scope.

    `MarketClosed` does not exist yet, and a top-level `from core.errors import
    MarketClosed` would be one collection error taking the whole file down as a
    single red line. Reached through here, every test below fails on its own,
    named after the criterion it holds (D-007). Same convention as
    `test_market_terms.py` and `market_service`'s `test_browsing.py`.
    """
    from core import errors  # noqa: PLC0415

    return errors


def test_market_closed_is_a_ledger_error() -> None:
    """The base class is what `register_error_handlers` dispatches on.

    `@app.exception_handler(LedgerError)` catches subclasses. An error that
    missed the hierarchy — a bare `Exception`, or one inheriting from
    `ValueError` — would escape the handler and surface as a 500 saying the
    ledger has a bug, on a request whose only problem is that the market shut.
    """
    assert issubclass(_errors().MarketClosed, _errors().LedgerError)


def test_market_closed_is_a_409() -> None:
    """409, for `InsufficientFunds`'s reason, which ADR 0017 cites by name.

    The request is well formed and it is the state that refuses it — the same
    request would have succeeded an hour ago and will never succeed again.
    Not 422, which in this service means the caller's parameters are wrong and
    a corrected request will work (`UnknownOutcome`, `UnbalancedTransaction`).
    Not 403, which is about who is asking.
    """
    assert _errors().MarketClosed.status_code == 409


def test_market_closed_spells_the_code_the_way_market_service_does() -> None:
    """`market_closed`, matching market_service's own code. ADR 0017.

    Not cosmetic. [FE] #49 renders the refusal with the handler it already has
    for the market service's version of this error, and a ledger that spelled
    it `market_not_open` or `trading_closed` would be a second string for one
    condition, in a frontend that has no way to know they mean the same thing.
    """
    assert _errors().MarketClosed.code == "market_closed"
