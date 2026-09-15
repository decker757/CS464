"""The HTTP surface. [4.3] #15

Status codes, query-string parsing, response shape and the guard. The filtering
and ordering rules themselves are business rules and are tested in
unit_test/service/test_reading.py, without HTTP.
"""

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
    """403, not 401.

    The log names accounts and quotes the reasons admins gave for acting on
    them. A trader's session is perfectly valid and will never be allowed in,
    and the two codes tell the frontend which of those it is looking at.
    """
    response = await client.get(_ACTIONS, headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_an_expired_token_is_refused(client: AsyncClient) -> None:
    token = mint_token(uuid.uuid4(), expires_in=-1)

    response = await client.get(_ACTIONS, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_a_token_from_another_issuer_is_refused(client: AsyncClient) -> None:
    """A token minted for a different system that happens to share our secret."""
    token = mint_token(uuid.uuid4(), issuer="somebody-else")

    response = await client.get(_ACTIONS, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


@pytest.mark.parametrize("role", ["super_admin", "resolver", "", None])
async def test_a_role_this_build_does_not_know_is_treated_as_a_trader(
    client: AsyncClient, role: str | None
) -> None:
    """Fail closed, and fail as 403 rather than 401.

    [4.4] #16 adds MARKET_CREATOR, RESOLVER and SUPER_ADMIN. During that
    rollout this service will see tokens carrying roles it has never heard of.
    A correctly signed token is not grounds for rejecting the session, and an
    unrecognised role is not grounds for granting authority, so it lands on the
    least privileged role and gets a 403. Signed here by hand because
    mint_token will not build a role that is not in the enum.
    """
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
    await seed(actor_id, 2, target_label="Will inflation be below 2%?")

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


async def test_every_timestamp_carries_an_offset(
    client: AsyncClient, admin_headers, actor_id: uuid.UUID, seed
) -> None:
    """Otherwise one entry serialises with a trailing Z and another without,
    and the frontend has to special-case which."""
    await seed(actor_id, 1)

    response = await client.get(
        _ACTIONS, headers=admin_headers, params={"actor_id": str(actor_id)}
    )

    occurred_at = response.json()["actions"][0]["occurred_at"]
    assert occurred_at.endswith("Z") or "+" in occurred_at


async def test_the_context_comes_back_as_an_object(
    client: AsyncClient, admin_headers, actor_id: uuid.UUID, seed
) -> None:
    await seed(actor_id, 1, context={"seed_subsidy": "250.0000", "outcomes": ["Yes", "No"]})

    response = await client.get(
        _ACTIONS, headers=admin_headers, params={"actor_id": str(actor_id)}
    )

    assert response.json()["actions"][0]["context"] == {
        "seed_subsidy": "250.0000",
        "outcomes": ["Yes", "No"],
    }


async def test_a_reason_is_returned_when_one_was_given(
    client: AsyncClient, admin_headers, actor_id: uuid.UUID, seed
) -> None:
    """[4.3] #15's first criterion. Null for the action types that do not ask
    for one; [2.3] #7 and [4.2] #14 are the ones that will."""
    await seed(actor_id, 1, reason="Duplicate of an existing market.")

    response = await client.get(
        _ACTIONS, headers=admin_headers, params={"actor_id": str(actor_id)}
    )

    assert response.json()["actions"][0]["reason"] == "Duplicate of an existing market."


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
    """A caller asking for more than the server will serve wants as much as it
    can get. A 422 on `limit=1000` would be a worse answer than the page the
    server is willing to return."""
    get_settings.cache_clear()
    monkeypatch.setenv("MAX_PAGE_SIZE", "3")
    await seed(actor_id, 5)

    response = await client.get(
        _ACTIONS,
        headers=admin_headers,
        params={"actor_id": str(actor_id), "limit": 1000},
    )
    get_settings.cache_clear()

    assert response.status_code == 200
    assert len(response.json()["actions"]) == 3


async def test_a_limit_of_zero_is_refused(client: AsyncClient, admin_headers) -> None:
    """Unlike an oversized limit, this one cannot be satisfied at all: a page of
    nothing makes no progress and no cursor can advance past it."""
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
    """[4.3] #15's second criterion, at the API surface.

    Entries are appended by the service performing the action, inside that
    action's transaction. Nothing reaches this table over HTTP — and a route
    added here in a hurry would still fail, because audit_svc holds no UPDATE
    or DELETE grant and a trigger refuses both even for the table's owner.
    """
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
