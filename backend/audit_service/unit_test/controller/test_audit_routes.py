"""The HTTP surface: status codes, query parsing, response shape, the guard. [4.3] #15"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient

from core.config import get_settings
from unit_test.conftest import mint_token

_ACTIONS = "/audit/actions"


# --- the guard ------------------------------------------------------------
async def test_an_anonymous_request_is_refused(client: AsyncClient) -> None:
    response = await client.get(_ACTIONS)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_a_trader_is_refused(client: AsyncClient, trader_headers) -> None:
    """403, not 401: the session is valid and will never be allowed in."""
    response = await client.get(_ACTIONS, headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_an_expired_token_is_refused(client: AsyncClient) -> None:
    token = mint_token(uuid.uuid4(), expires_in=-1)

    response = await client.get(_ACTIONS, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


@pytest.mark.parametrize("role", ["super_admin", "resolver", "", None])
async def test_a_role_this_build_does_not_know_is_treated_as_a_trader(
    client: AsyncClient, role: str | None
) -> None:
    """Fail closed, as 403 rather than 401: a valid signature keeps the
    session, an unknown role grants nothing. Signed by hand because mint_token
    only builds roles in the enum."""
    settings = get_settings()
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "username": "ernest_t",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=900),
    }
    if role is not None:
        claims["role"] = role
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    response = await client.get(_ACTIONS, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


async def test_a_browser_cookie_is_accepted(
    client: AsyncClient, admin_id: uuid.UUID
) -> None:
    """ADR 0002: cookies for browsers, bearer tokens for services."""
    client.cookies.set(get_settings().access_cookie_name, mint_token(admin_id))

    response = await client.get(_ACTIONS)

    assert response.status_code == 200


# --- the response ---------------------------------------------------------
async def test_it_returns_a_page(
    client: AsyncClient, admin_headers, actor_id: uuid.UUID, seed
) -> None:
    """[4.3] #15's first criterion: who, what, to what, when and why."""
    await seed(
        actor_id,
        2,
        target_label="Will inflation be below 2%?",
        reason="Duplicate of an existing market.",
        context={"seed_subsidy": "250.0000", "outcomes": ["Yes", "No"]},
    )

    response = await client.get(
        _ACTIONS, headers=admin_headers, params={"actor_id": str(actor_id)}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["actions"]) == 2
    assert body["has_more"] is False
    assert body["next_cursor"] is None

    entry = body["actions"][0]
    assert entry["actor_id"] == str(actor_id)
    assert entry["actor_username"] == "ernest_t"
    assert entry["action_type"] == "market.submitted"
    assert entry["target_label"] == "Will inflation be below 2%?"
    assert entry["source_service"] == "market_service"
    assert entry["reason"] == "Duplicate of an existing market."
    assert entry["context"] == {"seed_subsidy": "250.0000", "outcomes": ["Yes", "No"]}
    # An explicit offset, so the frontend never special-cases a naive timestamp.
    occurred_at = datetime.fromisoformat(entry["occurred_at"])
    assert occurred_at.tzinfo is not None


# --- filters --------------------------------------------------------------
async def test_it_filters_by_actor(
    client: AsyncClient, admin_headers, actor_id: uuid.UUID, seed
) -> None:
    await seed(actor_id, 2)
    await seed(uuid.uuid4(), 3)

    response = await client.get(
        _ACTIONS, headers=admin_headers, params={"actor_id": str(actor_id)}
    )

    assert len(response.json()["actions"]) == 2


async def test_it_filters_by_action_type(
    client: AsyncClient, admin_headers, actor_id: uuid.UUID, seed
) -> None:
    await seed(actor_id, 1, action_type="market.submitted")
    await seed(actor_id, 2, action_type="user.suspended")

    response = await client.get(
        _ACTIONS,
        headers=admin_headers,
        params={"actor_id": str(actor_id), "action_type": "user.suspended"},
    )

    assert len(response.json()["actions"]) == 2


async def test_an_actor_id_that_is_not_a_uuid_is_a_422(
    client: AsyncClient, admin_headers
) -> None:
    """Promised in docs/api/audit-service.md: FastAPI's own validation, a 422."""
    response = await client.get(
        _ACTIONS, headers=admin_headers, params={"actor_id": "ernest"}
    )

    assert response.status_code == 422


# --- paging ---------------------------------------------------------------
async def test_a_page_offers_a_cursor_and_the_next_page_uses_it(
    client: AsyncClient, admin_headers, actor_id: uuid.UUID, seed
) -> None:
    rows = await seed(actor_id, 5)
    params = {"actor_id": str(actor_id), "limit": 2}

    first = (await client.get(_ACTIONS, headers=admin_headers, params=params)).json()
    assert first["has_more"] is True

    second = (
        await client.get(
            _ACTIONS,
            headers=admin_headers,
            params={**params, "cursor": first["next_cursor"]},
        )
    ).json()

    assert [a["id"] for a in first["actions"]] == [str(r["id"]) for r in rows[:2]]
    assert [a["id"] for a in second["actions"]] == [str(r["id"]) for r in rows[2:4]]


async def test_a_limit_above_the_ceiling_is_clamped_rather_than_refused(
    client: AsyncClient, admin_headers, actor_id: uuid.UUID, seed, monkeypatch
) -> None:
    """A caller asking for more than the ceiling wants as much as it can get."""
    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PAGE_SIZE", "3")
    try:
        await seed(actor_id, 5)

        response = await client.get(
            _ACTIONS,
            headers=admin_headers,
            params={"actor_id": str(actor_id), "limit": 1000},
        )
    finally:
        # The settings cached under the patched environment must not leak.
        get_settings.cache_clear()

    assert response.status_code == 200
    assert len(response.json()["actions"]) == 3


async def test_a_limit_of_zero_is_refused(client: AsyncClient, admin_headers) -> None:
    """A page of nothing makes no progress, so it cannot be clamped."""
    response = await client.get(
        _ACTIONS, headers=admin_headers, params={"limit": 0}
    )

    assert response.status_code == 422


async def test_a_forged_cursor_is_a_400(client: AsyncClient, admin_headers) -> None:
    response = await client.get(
        _ACTIONS, headers=admin_headers, params={"cursor": "made-this-up"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_cursor"


# --- the shape of the API itself ------------------------------------------
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
async def test_there_is_no_write_route(
    client: AsyncClient, admin_headers, method: str
) -> None:
    """[4.3] #15's second criterion, at the API surface. ADR 0006."""
    response = await getattr(client, method)(_ACTIONS, headers=admin_headers)

    assert response.status_code == 405


async def test_the_openapi_document_advertises_one_read_route(
    client: AsyncClient,
) -> None:
    """/docs is the contract the admin console codes against."""
    document = (await client.get("/openapi.json")).json()

    assert set(document["paths"][_ACTIONS]) == {"get"}


async def test_health_needs_no_token(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
