"""Test-suite helpers every service's conftest needs. [F-6] #76

Imported before anything else in a conftest, and deliberately importing nothing
from a service: `load_repo_env` has to run before the first `core.config` import
in the process, because `get_settings` is `lru_cache`d and the first call wins.

Excluded from the runtime images by `backend/.dockerignore`. It is the one file
in this package that no service imports at runtime, and a module that only the
suites use has no business in a production layer.
"""

from __future__ import annotations

import os
import pathlib

# Marker files that identify the repository root. Searched for upward rather
# than reached by a fixed number of `parents[...]` hops: a hop count is correct
# only for this file's current depth, and if the package is ever moved it
# resolves to something like `/` instead — where `.env` is simply absent and
# `load_repo_env` becomes a silent no-op that every suite then inherits as
# "no test database configured".
_MARKERS = ("docker-compose.yml", ".git")


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
