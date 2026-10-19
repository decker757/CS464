"""The engine's URL. #307"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.engine import make_url

from core import database


def test_the_engine_is_built_from_the_configured_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#307 hides `database_url` from a printed Settings; the engine must still
    be built from the URL itself, password included. The expected value is the
    environment conftest configured, not Settings, so a reader that hands over
    the masked form cannot agree with itself. The cached engine is set aside,
    so this test builds its own and never connects."""
    monkeypatch.setattr(database, "_engine", None)

    assert database.get_engine().url == make_url(os.environ["DATABASE_URL"])
