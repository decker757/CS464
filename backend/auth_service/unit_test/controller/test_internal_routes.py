"""HTTP wiring for the service-to-service lookup: the key, and the shape. [L-1] #38, #306

ADR 0021. The rules about which ids are answered are in
`unit_test/service/test_user_directory.py`.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from core.config import get_settings
from unit_test.conftest import VALID_PASSWORD

LOOKUP = "/internal/users/lookup"


def _key() -> dict[str, str]:
    return {"X-Service-Key": get_settings().ledger_service_key.get_secret_value()}


@pytest.fixture
async def registered_id(client: AsyncClient) -> str:
    """A trader, registered over HTTP. Leaves the client holding their cookie."""
    response = await client.post(
        "/auth/register",
        json={
            "username": "michelle_l",
            "email": "michelle@example.com",
            "password": VALID_PASSWORD,
        },
    )
    user_id = response.json()["user"]["id"]
    client.cookies.clear()
    return user_id


# --- the answer -----------------------------------------------------------
async def test_the_right_key_opens_it_and_the_answer_is_exactly_three_fields(
    client: AsyncClient, registered_id: str
) -> None:
    response = await client.post(
        LOOKUP, json={"user_ids": [registered_id, str(uuid.uuid4())]}, headers=_key()
    )

    assert response.status_code == 200
    assert response.json() == {
        "users": [{"id": registered_id, "username": "michelle_l", "is_suspended": False}]
    }
    # The row also holds these; the contract names them as never leaving.
    for private in ("email", "role", "created_at", "password"):
        assert private not in response.text


# --- the key, and only the key --------------------------------------------
async def test_a_missing_key_is_401_invalid_service_key(
    client: AsyncClient, registered_id: str
) -> None:
    response = await client.post(LOOKUP, json={"user_ids": [registered_id]})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_service_key"


async def test_a_wrong_key_is_the_same_401_as_a_missing_one(
    client: AsyncClient, registered_id: str
) -> None:
    missing = await client.post(LOOKUP, json={"user_ids": [registered_id]})
    wrong = await client.post(
        LOOKUP,
        json={"user_ids": [registered_id]},
        headers={"X-Service-Key": "not-the-key"},
    )

    assert wrong.status_code == 401
    assert wrong.json() == missing.json()


async def test_an_administrators_access_token_does_not_open_it(
    admin_client: AsyncClient,
) -> None:
    token = admin_client.cookies.get("access_token")
    assert token is not None

    by_cookie = await admin_client.post(LOOKUP, json={"user_ids": [str(uuid.uuid4())]})
    admin_client.cookies.clear()
    by_bearer = await admin_client.post(
        LOOKUP,
        json={"user_ids": [str(uuid.uuid4())]},
        headers={"Authorization": f"Bearer {token}"},
    )

    for response in (by_cookie, by_bearer):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_service_key"


async def test_the_key_does_not_open_another_route(client: AsyncClient) -> None:
    response = await client.get("/admin/users", headers=_key())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_the_key_does_not_open_the_admin_lookup(
    client: AsyncClient, registered_id: str
) -> None:
    """The route most likely to be widened to take the key too: it answers
    the same question. [2.4] #273 is an administrator's, the key is the ledger's."""
    response = await client.post(
        "/admin/users/lookup", json={"user_ids": [registered_id]}, headers=_key()
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


# --- the request ----------------------------------------------------------
@pytest.mark.parametrize("count", [0, 501])
async def test_a_count_outside_one_to_five_hundred_is_422(
    client: AsyncClient, count: int
) -> None:
    ids = [str(uuid.uuid4()) for _ in range(count)]

    response = await client.post(LOOKUP, json={"user_ids": ids}, headers=_key())

    # FastAPI's own 422: auth has no `invalid_request` envelope (the ledger
    # does), and docs/api/auth-service.md lists this one as "a different shape".
    assert response.status_code == 422


async def test_five_hundred_ids_are_accepted(client: AsyncClient) -> None:
    ids = [str(uuid.uuid4()) for _ in range(500)]

    response = await client.post(LOOKUP, json={"user_ids": ids}, headers=_key())

    assert response.status_code == 200
    assert response.json() == {"users": []}


async def test_any_other_field_in_the_body_is_refused(
    client: AsyncClient, registered_id: str
) -> None:
    response = await client.post(
        LOOKUP,
        json={"user_ids": [registered_id], "include_email": True},
        headers=_key(),
    )

    # FastAPI's own 422: auth has no `invalid_request` envelope (the ledger
    # does), and docs/api/auth-service.md lists this one as "a different shape".
    assert response.status_code == 422
