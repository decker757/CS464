"""One market_service client for the process. #114, D-047.

`market_terms.fetch` runs on every trade that is not a replay (ADR 0017), so
a client per call paid a fresh DNS lookup and handshake on the hottest path.
Opened on the app lifespan in `main.py`, like the Redis client, and closed
with it.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx


def _app():
    """Imported inside each test, so a missing name fails the test that needs
    it rather than collecting the whole file (D-007)."""
    from main import create_app  # noqa: PLC0415

    return create_app()


async def test_one_client_serves_every_request_for_the_process(
    clean_database: None,
) -> None:
    """Built on startup and handed to every request by the dependency, with
    the base URL and D-030's timeout the per-call client had."""
    from controller.dependencies import get_terms_client  # noqa: PLC0415
    from core.config import get_settings  # noqa: PLC0415

    app = _app()

    async with app.router.lifespan_context(app):
        served = [
            await get_terms_client(SimpleNamespace(app=app)),
            await get_terms_client(SimpleNamespace(app=app)),
        ]

        assert served[0] is served[1], "a client per request is what #114 removes"
        assert isinstance(served[0], httpx.AsyncClient)
        assert not served[0].is_closed

        request = served[0].build_request("GET", "/public/markets/x")
        assert request.url == httpx.URL(get_settings().market_service_url).join(
            "/public/markets/x"
        )
        assert served[0].timeout == httpx.Timeout(5.0)


async def test_the_client_is_closed_when_the_app_shuts_down(
    clean_database: None,
) -> None:
    """Closed with the lifespan, not left to the garbage collector."""
    from controller.dependencies import get_terms_client  # noqa: PLC0415

    app = _app()

    async with app.router.lifespan_context(app):
        client = await get_terms_client(SimpleNamespace(app=app))

    assert client.is_closed
