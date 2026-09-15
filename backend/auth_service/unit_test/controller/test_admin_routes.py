"""HTTP wiring for the administrative routes. [4.4] #16

Business rules are asserted one layer down in unit_test/service. What is
checked here is only what the controller is responsible for: status codes, the
response envelope, and who the guard lets through.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from unit_test.conftest import VALID_PASSWORD


def _url(user_id: str) -> str:
    return f"/admin/users/{user_id}/role"


# --- the happy path -------------------------------------------------------
async def test_a_promotion_returns_200_and_the_updated_user(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    response = await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == target_user_id
    assert body["user"]["role"] == "admin"


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


async def test_the_response_never_contains_a_password_or_a_hash(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    response = await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    assert VALID_PASSWORD not in response.text
    assert "password" not in response.json()["user"]


async def test_a_promotion_binds_this_service_without_a_new_token(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """The administrator here is still carrying a token minted before promotion.

    It says `role: trader` and the route works anyway, because this service
    reads the row rather than the claim. Only services that cannot read
    auth.users — the market service — wait out the token's lifetime. ADR 0007
    records that asymmetry; this is it, asserted.
    """
    response = await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    assert response.status_code == 200


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
    """422 from Pydantic, before anything reaches the service layer.

    This is also what stops `super_admin` being granted by a client that read
    an old ticket: the vocabulary is closed, and widening it is a decision made
    in core/roles.py rather than in a request body.
    """
    response = await admin_client.patch(
        _url(target_user_id), json={"role": "super_admin"}
    )

    assert response.status_code == 422


async def test_a_malformed_user_id_is_rejected(admin_client: AsyncClient) -> None:
    response = await admin_client.patch(_url("not-a-uuid"), json={"role": "admin"})

    assert response.status_code == 422


# --- cookie reach ---------------------------------------------------------
async def test_the_access_cookie_reaches_this_prefix(
    admin_client: AsyncClient, target_user_id: str
) -> None:
    """These routes live under /admin, every other authenticated route under /auth.

    The access cookie is written with `path="/"` and reaches both. The refresh
    cookie is deliberately scoped to `refresh_cookie_path` so the long-lived
    credential is not attached to every request, and nothing here needs it.

    Asserted rather than assumed, because narrowing the access cookie's path to
    match its sibling's would 401 every admin route from a browser while
    leaving bearer-token callers — including most of this suite — working.
    """
    assert "Authorization" not in admin_client.headers
    assert admin_client.cookies.get("access_token") is not None

    response = await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    assert response.status_code == 200


async def test_a_suspended_administrator_gets_403(
    admin_client: AsyncClient, target_user_id: str, session
) -> None:
    """Suspension is checked before the role is, and reports as itself.

    `get_current_user` refuses a suspended account before `require_admin` ever
    looks at the role, so the caller is told their account is suspended rather
    than that they are not an administrator. The distinction matters: one is
    fixable by an administrator, the other is a lie.
    """
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
    """The 409 is not reachable this way, and that is the point.

    The caller is an administrator and cannot be their own target, so whenever
    a demotion gets this far there are at least two and one survives it. Only
    the concurrent case can reach `last_administrator`; it is asserted in
    unit_test/service/test_role_changes.py, where two transactions can be run
    against each other.
    """
    await admin_client.patch(_url(target_user_id), json={"role": "admin"})

    response = await admin_client.patch(_url(target_user_id), json={"role": "trader"})

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "trader"
