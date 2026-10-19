"""The composition root hands the bus its Redis URL. #307"""

from __future__ import annotations

import asyncio
import os

import pytest

import main


class _RecordingBus:
    """Stands in for `PriceBus` and keeps the URL it was built with. `run`
    waits to be cancelled, as the real one does, and never dials Redis."""

    def __init__(self, *, redis_url: object, hub: object, gate: object) -> None:
        self.redis_url = redis_url

    async def run(self) -> None:
        await asyncio.Event().wait()


async def test_the_bus_is_given_the_configured_redis_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#307 hides `redis_url` from a printed Settings; the bus must still be
    handed the URL itself. No other test catches a bus handed the masked form:
    its reconnect loop retries every failure, so the socket tests would wait
    rather than fail. The expected value is the environment conftest
    configured, not Settings."""
    monkeypatch.setattr(main, "PriceBus", _RecordingBus)
    app = main.create_app()

    async with app.router.lifespan_context(app):
        assert app.state.bus.redis_url == os.environ["REDIS_URL"]
