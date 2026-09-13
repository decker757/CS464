"""HTTP wiring for the five routes.

Business rules are asserted one layer down in unit_test/service. What is
checked here is only what the controller is responsible for: status codes, the
response envelope, and that a domain error reaches the client in one shape.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from unit_test.conftest import VALID_PASSWORD


async def test_register_returns_201_and_the_user(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    response = await client.post("/auth/register", json=registration_payload)

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["username"] == "ernest_t"
    assert body["tokens"]["token_type"] == "bearer"


async def test_no_response_ever_contains_the_password(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    response = await client.post("/auth/register", json=registration_payload)

    assert VALID_PASSWORD not in response.text
    assert "password" not in response.json()["user"]


async def test_a_duplicate_maps_to_409_in_the_error_envelope(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    await client.post("/auth/register", json=registration_payload)

    second = await client.post("/auth/register", json=registration_payload)

    assert second.status_code == 409
    assert second.json() == {
        "error": {
            "code": "duplicate_user",
            "message": "That username is already registered.",
        }
    }


@pytest.mark.parametrize(
    "bad_field",
    [
        {"password": "short"},
        {"email": "not-an-email"},
        {"username": "ab"},
        {"username": "has spaces"},
    ],
)
async def test_schema_violations_map_to_422(
    client: AsyncClient, registration_payload: dict[str, str], bad_field: dict[str, str]
) -> None:
    response = await client.post("/auth/register", json={**registration_payload, **bad_field})

    assert response.status_code == 422


@pytest.mark.parametrize("missing", ["username", "email", "password"])
async def test_an_absent_required_field_is_422(
    client: AsyncClient, registration_payload: dict[str, str], missing: str
) -> None:
    """[A-1] #29: registration requires all three, not just valid-if-present."""
    payload = {k: v for k, v in registration_payload.items() if k != missing}

    response = await client.post("/auth/register", json=payload)

    assert response.status_code == 422


async def test_login_returns_200_and_the_same_envelope_as_register(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    await client.post("/auth/register", json=registration_payload)

    response = await client.post(
        "/auth/login", json={"identifier": "ernest_t", "password": VALID_PASSWORD}
    )

    assert response.status_code == 200
    assert set(response.json()) == {"user", "tokens"}


async def test_a_failed_login_is_401_and_identical_for_both_causes(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    await client.post("/auth/register", json=registration_payload)

    wrong_password = await client.post(
        "/auth/login", json={"identifier": "ernest_t", "password": "wrong-password-here"}
    )
    unknown_account = await client.post(
        "/auth/login", json={"identifier": "nobody_at_all", "password": "wrong-password-here"}
    )

    assert wrong_password.status_code == unknown_account.status_code == 401
    assert wrong_password.json() == unknown_account.json()
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


async def test_a_suspended_account_maps_to_403(
    client: AsyncClient, registration_payload: dict[str, str], session
) -> None:
    from sqlalchemy import select

    from model.entities import User

    await client.post("/auth/register", json=registration_payload)
    user = (await session.execute(select(User))).scalar_one()
    user.is_suspended = True
    await session.commit()

    response = await client.post(
        "/auth/login", json={"identifier": "ernest_t", "password": VALID_PASSWORD}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_suspended"


async def test_an_unauthenticated_request_is_401_with_a_challenge_header(
    client: AsyncClient,
) -> None:
    response = await client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_token"


async def test_logout_always_reports_success(client: AsyncClient) -> None:
    """Never reveals whether a live session was actually found."""
    response = await client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out."}


async def test_refresh_without_a_token_is_401(client: AsyncClient) -> None:
    response = await client.post("/auth/refresh")

    assert response.status_code == 401


async def test_health_needs_no_credentials(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
