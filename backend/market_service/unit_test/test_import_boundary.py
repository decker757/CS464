"""No service imports another service. [F-6] #76, ADR 0012.

A guard, not decoration. `pytest.ini`'s `..` puts every other service on the
path, so a cross-service import passes CI here and is an ImportError in the
container, which holds only this service and `shared/`.
"""

from __future__ import annotations

import pathlib
import re

_THIS_SERVICE = "market_service"

_OTHER_SERVICES = [
    "auth_service",
    "market_service",
    "audit_service",
    "ledger_service",
    "realtime_service",
]

_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(" + "|".join(_OTHER_SERVICES) + r")\b", re.MULTILINE
)


def test_this_service_imports_no_other_service() -> None:
    service_root = pathlib.Path(__file__).resolve().parents[1]

    offenders: list[str] = []
    for path in service_root.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        for match in _IMPORT.finditer(path.read_text()):
            if match.group(1) != _THIS_SERVICE:
                offenders.append(f"{path.relative_to(service_root)}: {match.group(0).strip()}")

    assert offenders == [], (
        "a cross-service import works under pytest and fails in the container:\n"
        + "\n".join(offenders)
    )
