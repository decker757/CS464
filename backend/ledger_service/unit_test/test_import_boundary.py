"""No service imports another service. [F-6] #76, ADR 0012.

The scan and the reason it exists are `shared.testing.list_cross_service_imports`.
"""

from __future__ import annotations

from shared.testing import list_cross_service_imports


def test_this_service_imports_no_other_service() -> None:
    offenders = list_cross_service_imports("ledger_service")

    assert offenders == [], (
        "a cross-service import works under pytest and fails in the container:\n"
        + "\n".join(offenders)
    )
