"""One auth client for the process. [4.2] #14, ADR 0022.

The account check calls auth on every trade that is not a replay, so the
client is opened on the app lifespan beside the terms client, and closed with
it. DECISIONS.md, "One auth client in the ledger, for #14's trade check and
#38's leaderboard".
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx


async def test_one_auth_client_serves_every_request_and_closes_with_the_app(
    clean_database: None,
) -> None:
    from controller.dependencies import get_auth_client  # noqa: PLC0415
    from core.config import get_settings  # noqa: PLC0415
    from main import create_app  # noqa: PLC0415

    app = create_app()

    async with app.router.lifespan_context(app):
        served = [
            await get_auth_client(SimpleNamespace(app=app)),
            await get_auth_client(SimpleNamespace(app=app)),
        ]

        assert served[0] is served[1], "a client per request pays a handshake per trade"
        request = served[0].build_request("GET", "/auth/me")
        assert request.url == httpx.URL(get_settings().auth_service_url).join("/auth/me")
        assert served[0].timeout == httpx.Timeout(5.0)

    assert served[0].is_closed
