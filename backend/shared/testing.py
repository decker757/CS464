"""Test-suite helpers every service's suite needs. [F-6] #76

Deliberately importing nothing from a service. A conftest imports this before
anything else, because `load_repo_env` has to run before the first `core.config`
import in the process: `get_settings` is `lru_cache`d and the first call wins.

Excluded from the runtime images by `backend/.dockerignore`. It is the one file
in this package that no service imports at runtime, and a module that only the
suites use has no business in a production layer.
"""

from __future__ import annotations

import os
import pathlib
import re

# Marker files that identify the repository root. Searched for upward rather
# than reached by a fixed number of `parents[...]` hops: a hop count is correct
# only for this file's current depth, and if the package is ever moved it
# resolves to something like `/` instead — where `.env` is simply absent and
# `load_repo_env` becomes a silent no-op that every suite then inherits as
# "no test database configured".
_MARKERS = ("docker-compose.yml", ".git")

# Every service directory under `backend/`. A new service (the trading
# composite) is added here, or nothing stops the others importing it, and
# its copied test_import_boundary.py must pass its own name, not a sibling's.
_SERVICES = [
    "auth_service",
    "market_service",
    "audit_service",
    "ledger_service",
    "realtime_service",
]

_SERVICE_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(" + "|".join(_SERVICES) + r")\b", re.MULTILINE
)


def _repo_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    for candidate in here.parents:
        if any((candidate / marker).exists() for marker in _MARKERS):
            return candidate
    raise RuntimeError(
        f"Could not find the repository root above {here}. "
        f"Expected one of {_MARKERS} in a parent directory."
    )


def load_repo_env() -> None:
    """Read the repo-root .env, the same file docker compose reads.

    Real connection details live there and it is gitignored, so nothing in the
    repository carries a credential. Anything already exported wins, which is
    what lets CI set the variables directly without a file.

    A missing .env is not an error — that is exactly the CI case.
    """
    root_env = _repo_root() / ".env"
    if not root_env.is_file():
        return
    for line in root_env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def list_cross_service_imports(service: str) -> list[str]:
    """List every import in `backend/<service>/` that names another service.

    Each entry is "path: line", the path relative to the service directory.
    Raises ValueError for a name not in `_SERVICES`, so a typo cannot scan
    nothing and pass.

    `pythonpath = . ..` puts every other service on the path under pytest, so
    such an import passes CI and is an ImportError in the container, which
    holds only this service and `shared/`. Leaf modules (a schema,
    `core/roles.py`) are the ones that slip through. Services share code only
    through `backend/shared/`. ADR 0012.
    """
    if service not in _SERVICES:
        raise ValueError(f"{service!r} is not one of {_SERVICES}")

    service_root = _repo_root() / "backend" / service
    offenders: list[str] = []
    for path in service_root.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        for match in _SERVICE_IMPORT.finditer(path.read_text()):
            if match.group(1) != service:
                offenders.append(f"{path.relative_to(service_root)}: {match.group(0).strip()}")
    return offenders
