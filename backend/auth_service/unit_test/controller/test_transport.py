"""Cookies for the browser, bearer tokens for services.

The decision and its rejected alternatives: docs/adr/0002-auth-token-transport.md
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient

from core.config import get_settings
from unit_test.conftest import VALID_PASSWORD


async def _register(client: AsyncClient, payload: dict[str, str]) -> dict:
    return (await client.post("/auth/register", json=payload)).json()


async def test_register_sets_both_cookies_with_safe_attributes(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    response = await client.post("/auth/register", json=registration_payload)

    cookies = response.headers.get_list("set-cookie")
    access = next(c for c in cookies if c.startswith("access_token="))
    refresh = next(c for c in cookies if c.startswith("refresh_token="))

    assert "HttpOnly" in access and "HttpOnly" in refresh
    assert "SameSite=lax" in access
    assert "Path=/;" in access
    # Scoped to /auth so it never rides along on ordinary API calls, but still
    # reaches /auth/logout, which is what revokes it.
    assert "Path=/auth" in refresh


async def test_a_protected_route_accepts_the_browser_cookie(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    await _register(client, registration_payload)

    response = await client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["username"] == "ernest_t"


async def test_a_protected_route_accepts_a_bearer_header(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    body = await _register(client, registration_payload)
    client.cookies.clear()

    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {body['tokens']['access_token']}"}
    )

    assert response.status_code == 200


async def test_the_header_wins_over_the_cookie(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    """An explicit credential must never be reinterpreted as the ambient one."""
    await _register(client, registration_payload)
    assert client.cookies.get("access_token")

    response = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.real.token"})

    assert response.status_code == 401


async def test_a_forged_token_is_refused(client: AsyncClient) -> None:
    response = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.real.token"})

    assert response.status_code == 401


async def test_logout_clears_both_cookies(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    await _register(client, registration_payload)

    await client.post("/auth/logout")

    assert not client.cookies.get("access_token")
    assert (await client.get("/auth/me")).status_code == 401


async def test_the_refresh_cookie_reaches_logout(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    """Regression: scoped to /auth/refresh, logout received nothing and
    silently revoked nothing while still returning 200."""
    body = await _register(client, registration_payload)

    await client.post("/auth/logout")
    replay = await client.post(
        "/auth/refresh", headers={"X-Refresh-Token": body["tokens"]["refresh_token"]}
    )

    assert replay.status_code == 401


async def test_refresh_over_the_header_works_for_non_browser_callers(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    body = await _register(client, registration_payload)
    client.cookies.clear()

    response = await client.post(
        "/auth/refresh", headers={"X-Refresh-Token": body["tokens"]["refresh_token"]}
    )

    assert response.status_code == 200
    assert response.json()["tokens"]["refresh_token"] != body["tokens"]["refresh_token"]


async def test_logging_in_again_after_logout_restores_access(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    await _register(client, registration_payload)
    await client.post("/auth/logout")

    again = await client.post(
        "/auth/login", json={"identifier": "ernest_t", "password": VALID_PASSWORD}
    )

    assert again.status_code == 200
    assert (await client.get("/auth/me")).status_code == 200


async def test_timestamps_are_utc_on_every_route(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    """Register serialises an in-memory datetime, /auth/me one read back from
    the driver. Both must match or the frontend has to branch."""
    registered = await _register(client, registration_payload)
    fetched = await client.get("/auth/me")

    assert registered["user"]["created_at"].endswith("Z")
    assert fetched.json()["created_at"] == registered["user"]["created_at"]


async def test_an_expired_access_token_is_refused(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    """[A-3] #31: a refresh preserves access ONLY while the session is valid.

    Minted with a past expiry rather than waiting out the real 15-minute TTL.
    """
    body = await _register(client, registration_payload)
    settings = get_settings()
    past = datetime.now(UTC) - timedelta(hours=2)
    expired = jwt.encode(
        {
            "sub": body["user"]["id"],
            "username": body["user"]["username"],
            "iss": settings.jwt_issuer,
            "iat": past,
            "exp": past + timedelta(minutes=15),
            "jti": uuid.uuid4().hex,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    client.cookies.clear()

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


async def test_logout_does_not_revoke_an_already_issued_access_token(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    """Pins a KNOWN LIMITATION rather than asserting desired behaviour.

    [A-3] #31 says logout invalidates the session. A signed JWT cannot be
    withdrawn before it expires, so a caller who kept the bearer token keeps
    access for up to one access-token lifetime. Browsers are unaffected: their
    cookie is cleared. The 15-minute TTL is what bounds the window.

    If this test ever starts failing, someone added a revocation denylist and
    should delete it. See docs/adr/0002-auth-token-transport.md.
    """
    body = await _register(client, registration_payload)
    stolen = body["tokens"]["access_token"]

    await client.post("/auth/logout")
    client.cookies.clear()

    still_valid = await client.get("/auth/me", headers={"Authorization": f"Bearer {stolen}"})

    assert still_valid.status_code == 200
