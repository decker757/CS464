"""HTTP wiring for the administrative routes: status codes and the guard. [4.4] #16, [4.1] #13, [4.2] #14, [2.4] #273"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from unit_test.conftest import VALID_PASSWORD

USERS = "/admin/users"
LOOKUP = "/admin/users/lookup"


def _url(user_id: str) -> str:
    return f"/admin/users/{user_id}/role"


def _suspension_url(user_id: str) -> str:
    return f"/admin/users/{user_id}/suspension"


SUSPEND = {"suspended": True, "reason": "Second account of bob_1, same device."}


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


# --- suspension [4.2] #14 -------------------------------------------------
async def test_a_suspension_returns_200_with_the_row_the_list_shows(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """`AdminUserOut`, so the screen can replace the row, and the window the
    other services may still honour the user's token. ADR 0003, ADR 0022."""
    from core.config import get_settings  # noqa: PLC0415

    response = await admin_client.patch(_suspension_url(target_user_id), json=SUSPEND)

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == target_user_id
    assert body["user"]["is_suspended"] is True
    assert (
        body["takes_effect_within_seconds"]
        == get_settings().access_token_ttl_seconds
    )


async def test_an_anonymous_caller_cannot_suspend(
    client: AsyncClient, target_user_id: str
) -> None:
    response = await client.patch(_suspension_url(target_user_id), json=SUSPEND)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_trader_cannot_suspend(client: AsyncClient, target_user_id: str) -> None:
    await client.post(
        "/auth/register",
        json={
            "username": "just_a_trader",
            "email": "trader@example.com",
            "password": VALID_PASSWORD,
        },
    )

    response = await client.patch(_suspension_url(target_user_id), json=SUSPEND)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_an_administrator_suspending_themselves_gets_403(
    admin_client: AsyncClient,
) -> None:
    me = await admin_client.get("/auth/me")

    response = await admin_client.patch(_suspension_url(me.json()["id"]), json=SUSPEND)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "cannot_suspend_self"


async def test_suspending_an_unknown_user_gets_404(admin_client: AsyncClient) -> None:
    response = await admin_client.patch(_suspension_url(str(uuid.uuid4())), json=SUSPEND)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


async def test_a_short_reason_is_422_naming_the_field(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """[4.2] #14: requires a reason. Our envelope, with `details` on the field."""
    response = await admin_client.patch(
        _suspension_url(target_user_id), json={"suspended": True, "reason": "  spam  "}
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "suspension_incomplete"
    assert error["details"][0]["field"] == "reason"


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


async def test_a_role_narrows_the_list(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """That `role` reaches the service layer; filtering is tested there."""
    response = await admin_client.get(USERS, params={"role": "admin"})

    assert [user["username"] for user in response.json()["users"]] == ["admin_one"]


@pytest.mark.parametrize(
    ("is_suspended", "expected"), [("true", ["michelle_l"]), ("false", ["admin_one"])]
)
async def test_a_suspension_state_narrows_the_list(
    admin_client: AsyncClient,
    target_user_id: str,
    session,
    is_suspended: str,
    expected: list[str],
) -> None:
    """That `is_suspended` reaches the service layer; filtering is tested there."""
    from sqlalchemy import text  # noqa: PLC0415

    await session.execute(
        text("UPDATE auth.users SET is_suspended = true WHERE lower(username) = 'michelle_l'")
    )
    await session.commit()

    response = await admin_client.get(USERS, params={"is_suspended": is_suspended})

    assert [user["username"] for user in response.json()["users"]] == expected


async def test_an_unknown_role_filter_is_422_naming_the_parameter(
    admin_client: AsyncClient,
) -> None:
    """The vocabulary is closed, so `super_admin` is a caller error, not an
    empty list that reads as "nobody holds it". ADR 0007."""
    response = await admin_client.get(USERS, params={"role": "super_admin"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "role"]


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


# --- the lookup [2.4] #273 ------------------------------------------------
async def test_an_administrator_gets_each_known_id_once_as_id_and_username_only(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """A duplicate is answered once, an unknown id is left out, and nothing
    else of the row leaves: no email, role, `is_suspended` or `created_at`."""
    response = await admin_client.post(
        LOOKUP,
        json={"user_ids": [target_user_id, target_user_id, str(uuid.uuid4())]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "users": [{"id": target_user_id, "username": "michelle_l"}]
    }


async def test_a_suspended_user_is_named(
    admin_client: AsyncClient, target_user_id: str, session
) -> None:
    """The trader an administrator is most likely investigating."""
    from sqlalchemy import text  # noqa: PLC0415

    await session.execute(
        text("UPDATE auth.users SET is_suspended = true WHERE lower(username) = 'michelle_l'")
    )
    await session.commit()
    me = await admin_client.get("/auth/me")

    response = await admin_client.post(
        LOOKUP, json={"user_ids": [me.json()["id"], target_user_id]}
    )

    assert response.status_code == 200
    # No order is promised, so compare as a set.
    assert {(user["id"], user["username"]) for user in response.json()["users"]} == {
        (me.json()["id"], "admin_one"),
        (target_user_id, "michelle_l"),
    }


@pytest.mark.parametrize("count", [0, 201])
async def test_a_count_outside_one_to_two_hundred_is_422(
    admin_client: AsyncClient, count: int
) -> None:
    ids = [str(uuid.uuid4()) for _ in range(count)]

    response = await admin_client.post(LOOKUP, json={"user_ids": ids})

    # FastAPI's own 422: auth has no `invalid_request` envelope (the ledger
    # does), and docs/api/auth-service.md lists this one as "a different shape".
    assert response.status_code == 422


@pytest.mark.parametrize("count", [1, 200])
async def test_one_and_two_hundred_ids_are_accepted(
    admin_client: AsyncClient, target_user_id: str, count: int
) -> None:
    """Both edges of 1 to 200. The single id names a real user, so the answer
    shows the request was read rather than only let through."""
    ids = [target_user_id] + [str(uuid.uuid4()) for _ in range(count - 1)]

    response = await admin_client.post(LOOKUP, json={"user_ids": ids})

    assert response.status_code == 200
    assert response.json() == {
        "users": [{"id": target_user_id, "username": "michelle_l"}]
    }


async def test_an_entry_that_is_not_a_uuid_is_422(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """Beside a valid id, so only the bad entry can be what is refused."""
    response = await admin_client.post(
        LOOKUP, json={"user_ids": [target_user_id, "5f3e"]}
    )

    assert response.status_code == 422


async def test_an_unknown_field_in_the_body_is_422(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """So a caller cannot believe it asked for more than two fields."""
    response = await admin_client.post(
        LOOKUP, json={"user_ids": [target_user_id], "include_email": True}
    )

    assert response.status_code == 422


async def test_an_anonymous_caller_cannot_look_up_users(
    client: AsyncClient, target_user_id: str
) -> None:
    response = await client.post(LOOKUP, json={"user_ids": [target_user_id]})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_trader_cannot_look_up_users(
    client: AsyncClient, target_user_id: str
) -> None:
    await client.post(
        "/auth/register",
        json={
            "username": "nosy_trader",
            "email": "nosy@example.com",
            "password": VALID_PASSWORD,
        },
    )

    response = await client.post(LOOKUP, json={"user_ids": [target_user_id]})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_a_lookup_makes_no_audit_entry(
    admin_client: AsyncClient, target_user_id: str, audit_reader
) -> None:
    """A read, like the search: the log records decisions. The admin's id is
    fresh per test, which is the scope ADR 0006 requires of the log."""
    from sqlalchemy import text  # noqa: PLC0415

    admin_id = (await admin_client.get("/auth/me")).json()["id"]

    response = await admin_client.post(LOOKUP, json={"user_ids": [target_user_id]})

    assert response.status_code == 200
    entries = await audit_reader.execute(
        text("SELECT count(*) FROM audit.admin_actions WHERE actor_id = :actor"),
        {"actor": uuid.UUID(admin_id)},
    )
    assert entries.scalar_one() == 0
