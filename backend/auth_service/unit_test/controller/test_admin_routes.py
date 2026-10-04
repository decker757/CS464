"""HTTP wiring for the administrative routes: status codes and the guard. [4.4] #16, [4.1] #13"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from unit_test.conftest import VALID_PASSWORD

USERS = "/admin/users"


def _url(user_id: str) -> str:
    return f"/admin/users/{user_id}/role"


# --- the happy path -------------------------------------------------------
async def test_a_promotion_returns_200_and_the_updated_user(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """Sent by browser cookie, carrying a token that still says `trader`.

    The cookie must reach /admin as well as /auth, and this service reads the
    row rather than the claim. ADR 0007. The only test of this route's body, so
    it also checks that no password or hash comes back.
    """
    assert "Authorization" not in admin_client.headers
    assert admin_client.cookies.get("access_token") is not None

    response = await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == target_user_id
    assert body["user"]["role"] == "admin"
    assert VALID_PASSWORD not in response.text
    assert "password" not in body["user"]


async def test_it_reports_how_long_the_old_authority_can_linger(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """Never zero. Authority rides in the token, so other services lag. ADR 0003."""
    from core.config import get_settings  # noqa: PLC0415

    response = await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    assert (
        response.json()["takes_effect_within_seconds"]
        == get_settings().access_token_ttl_seconds
    )


async def test_a_reason_is_accepted_and_is_not_required(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    with_reason = await admin_client.patch(
        _url(target_user_id), json={"role": "admin", "reason": "Covering resolution."}
    )
    without_reason = await admin_client.patch(
        _url(target_user_id), json={"role": "trader"}
    )

    assert with_reason.status_code == 200
    assert without_reason.status_code == 200


# --- the guard ------------------------------------------------------------
async def test_an_anonymous_caller_gets_401(
    client: AsyncClient, target_user_id: str
) -> None:
    response = await client.patch(_url(target_user_id), json={"role": "admin"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_trader_gets_403(client: AsyncClient, target_user_id: str) -> None:
    """Registered, signed in, and still not entitled. Distinct from 401."""
    await client.post(
        "/auth/register",
        json={
            "username": "just_a_trader",
            "email": "trader@example.com",
            "password": VALID_PASSWORD,
        },
    )

    response = await client.patch(_url(target_user_id), json={"role": "admin"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_an_administrator_targeting_themselves_gets_403(
    admin_client: AsyncClient
) -> None:
    me = await admin_client.get("/auth/me")

    response = await admin_client.patch(
        _url(me.json()["id"]), json={"role": "trader"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "cannot_change_own_role"


# --- bad input ------------------------------------------------------------
async def test_an_unknown_user_gets_404(admin_client: AsyncClient) -> None:
    response = await admin_client.patch(_url(str(uuid.uuid4())), json={"role": "admin"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


async def test_an_unknown_role_is_rejected_by_the_schema(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """The role vocabulary is closed; `super_admin` cannot be granted. ADR 0007."""
    response = await admin_client.patch(
        _url(target_user_id), json={"role": "super_admin"}
    )

    assert response.status_code == 422


# --- suspension and demotion ---------------------------------------------
async def test_a_suspended_administrator_gets_403(
    admin_client: AsyncClient, target_user_id: str, session
) -> None:
    """Suspension is checked before the role, and reports as itself."""
    from sqlalchemy import text  # noqa: PLC0415

    await session.execute(
        text("UPDATE auth.users SET is_suspended = true WHERE lower(username) = 'admin_one'")
    )
    await session.commit()

    response = await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_suspended"


async def test_demoting_the_other_administrator_is_allowed(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """Sequentially the 409 is unreachable; only the race reaches it. ADR 0007."""
    await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    response = await admin_client.patch(_url(target_user_id), json={"role": "trader"})

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "trader"


# --- the user list --------------------------------------------------------
async def test_an_administrator_lists_every_account(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """[4.1] #13's first criterion. The page an administrator opens on."""
    response = await admin_client.get(USERS)

    assert response.status_code == 200
    body = response.json()
    assert {user["username"] for user in body["users"]} == {"admin_one", "michelle_l"}
    assert body["has_more"] is False
    assert body["next_cursor"] is None


async def test_a_query_narrows_the_list(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """That `q` reaches the service layer; matching is tested there."""
    response = await admin_client.get(USERS, params={"q": "michelle"})

    assert [user["username"] for user in response.json()["users"]] == ["michelle_l"]


async def test_the_list_carries_the_id_the_ledger_takes(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """The `user_id` the ledger's history route takes, and no balance.

    Plus the suspension flag, written by [4.2] #14; why `AdminUserOut` exists.
    """
    body = (await admin_client.get(USERS, params={"q": "michelle"})).json()

    assert body["users"][0]["id"] == target_user_id
    assert "balance" not in body["users"][0]
    assert body["users"][0]["is_suspended"] is False


async def test_the_list_never_contains_a_password_or_a_hash(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """The one route returning many users, so where a leak would be in bulk."""
    response = await admin_client.get(USERS)

    assert VALID_PASSWORD not in response.text
    assert "password" not in response.text
    assert all("password" not in user for user in response.json()["users"])


async def test_an_anonymous_caller_cannot_list_users(client: AsyncClient) -> None:
    response = await client.get(USERS)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_trader_cannot_list_users(client: AsyncClient) -> None:
    """Who else holds an account is not a trader's business."""
    await client.post(
        "/auth/register",
        json={
            "username": "nosy_trader",
            "email": "nosy@example.com",
            "password": VALID_PASSWORD,
        },
    )

    response = await client.get(USERS)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_a_cursor_we_did_not_issue_is_a_400(admin_client: AsyncClient) -> None:
    response = await admin_client.get(USERS, params={"cursor": "nonsense"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_cursor"


async def test_an_oversized_limit_is_clamped_not_refused(
    admin_client: AsyncClient,
) -> None:
    """A caller asking for more than the ceiling wants as much as it can get.

    Promised in docs/api/auth-service.md: a 200, never a 422.
    """
    assert (await admin_client.get(USERS, params={"limit": 100000})).status_code == 200


async def test_a_zero_limit_is_refused(admin_client: AsyncClient) -> None:
    """A page of nothing is a caller bug, so it is refused, not clamped."""
    assert (await admin_client.get(USERS, params={"limit": 0})).status_code == 422
