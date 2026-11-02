"""The engine's pool, sized from the settings. #19"""

from __future__ import annotations

import pytest

from core import database
from core.config import get_settings


def test_the_engine_pool_is_sized_from_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#19: production sizes every pool so all replicas of all services fit
    under the managed database's one connection ceiling. An engine that kept
    its own numbers would open up to twenty per process and exhaust it. The
    cached Settings and engine are set aside, so nothing here connects."""
    monkeypatch.setenv("DATABASE_POOL_SIZE", "3")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "1")
    monkeypatch.setattr(database, "_engine", None)
    get_settings.cache_clear()

    try:
        pool = database.get_engine().pool
        assert pool.size() == 3
        # QueuePool has no public reader for its overflow limit.
        assert pool._max_overflow == 1  # noqa: SLF001
    finally:
        get_settings.cache_clear()
