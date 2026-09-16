"""No service imports another service. [F-6] #76

An architectural guard, and one this suite needs specifically because of how
`shared/` was made importable. `pytest.ini` says `pythonpath = . ..`, and that
`..` is `backend/` — which puts every *other* service on the path too, as an
implicit namespace package. `import market_service.core.roles` from here
succeeds under pytest.

It does not succeed in the container. The image holds `/app/<this service>` and
`/app/shared` and nothing else, so a cross-service import is an ImportError at
startup. That asymmetry is the whole reason for this test: without it the
failure mode is code that passes CI and dies on deploy.

Most cross-service imports already fail loudly on their own — `core` resolves
to *this* service's `core`, so anything with intra-service imports explodes
immediately. The ones that slip through are leaf modules, which is exactly the
shape somebody would reach for: `model/audit.py`, `core/roles.py`, a schema.

The rule this enforces is CLAUDE.md's: services share no code except
`backend/shared/`, which is the one package everything is allowed to import.
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
