"""One auth client for the process. [4.2] #14, ADR 0022.

The leaderboard's user directory and the local copy's refresh of suspended
ids both call auth, so the client is opened on the app lifespan beside the
terms client, and closed with it. DECISIONS.md, "One auth client in the
ledger, for #14's trade check and #38's leaderboard".
"""

from __future__ import annotations

import httpx


async def test_one_auth_client_is_opened_for_the_process_and_closes_with_the_app(
    clean_database: None,
) -> None:
    from core.config import get_settings  # noqa: PLC0415
    from main import create_app  # noqa: PLC0415

    app = create_app()

    async with app.router.lifespan_context(app):
        client = app.state.auth_client

        request = client.build_request("GET", "/auth/me")
        assert request.url == httpx.URL(get_settings().auth_service_url).join("/auth/me")
        assert client.timeout == httpx.Timeout(5.0)

    assert client.is_closed
