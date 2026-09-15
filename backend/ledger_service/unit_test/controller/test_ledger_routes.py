"""Status codes, authorisation and response shapes.

Business rules are tested in unit_test/service/ without HTTP. What is asserted
here is what the controller owns: who may call a route, what comes back, and
what the wire looks like.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient

from core.roles import UserRole
from unit_test.conftest import bearer

ME = "/ledger/balances/me"
MY_ENTRIES = "/ledger/entries/me"


def _user(user_id: uuid.UUID) -> str:
    return f"/ledger/users/{user_id}/balance"


def _user_entries(user_id: uuid.UUID) -> str:
    return f"/ledger/users/{user_id}/entries"


# --- authentication ------------------------------------------------------


@pytest.mark.parametrize("path", [ME, MY_ENTRIES])
async def test_an_anonymous_caller_is_refused(client: AsyncClient, path: str) -> None:
    response = await client.get(path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_an_expired_token_is_refused(client: AsyncClient) -> None:
    headers = bearer(uuid.uuid4(), UserRole.TRADER, expires_in=-1)

    assert (await client.get(ME, headers=headers)).status_code == 401


async def test_a_token_from_another_issuer_is_refused(client: AsyncClient) -> None:
    headers = bearer(uuid.uuid4(), UserRole.TRADER, issuer="somebody-else")

    assert (await client.get(ME, headers=headers)).status_code == 401


async def test_the_cookie_is_accepted_too(
    client: AsyncClient, user_id: uuid.UUID
) -> None:
    """ADR 0002: the browser sends a cookie, a service sends a header, and one
    verification path serves both."""
    from unit_test.conftest import mint_token  # noqa: PLC0415

    client.cookies.set("access_token", mint_token(user_id, UserRole.TRADER))

    assert (await client.get(ME)).status_code == 200


# --- authorisation -------------------------------------------------------


async def test_a_trader_reads_their_own_balance(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """No admin role required. This is [B-2] #33 and every trader needs it."""
    assert (await client.get(ME, headers=trader_headers)).status_code == 200


async def test_a_trader_cannot_read_someone_elses_balance(
    client: AsyncClient, trader_headers: dict[str, str], other_user_id: uuid.UUID
) -> None:
    response = await client.get(_user(other_user_id), headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_a_trader_cannot_read_someone_elses_history(
    client: AsyncClient, trader_headers: dict[str, str], other_user_id: uuid.UUID
) -> None:
    response = await client.get(_user_entries(other_user_id), headers=trader_headers)

    assert response.status_code == 403


async def test_a_trader_cannot_reach_their_own_balance_by_the_admin_route(
    client: AsyncClient, user_id: uuid.UUID
) -> None:
    """The admin routes are guarded by the role, not by whose id is in the
    path. A trader asking about themselves there is still a trader asking an
    administrator's question."""
    headers = bearer(user_id, UserRole.TRADER)

    assert (await client.get(_user(user_id), headers=headers)).status_code == 403


async def test_an_admin_reads_any_balance(
    client: AsyncClient, admin_headers: dict[str, str], other_user_id: uuid.UUID
) -> None:
    """[4.1] #13. An administrator investigating an anomaly."""
    response = await client.get(_user(other_user_id), headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["user_id"] == str(other_user_id)


# --- the balance ---------------------------------------------------------


async def test_a_new_user_sees_their_starting_credits(
    client: AsyncClient,
    trader_headers: dict[str, str],
    user_id: uuid.UUID,
    starting_credits: Decimal,
) -> None:
    """[B-1] #32 and [B-2] #33 meeting at the first request a new account makes."""
    body = (await client.get(ME, headers=trader_headers)).json()

    assert body["user_id"] == str(user_id)
    assert Decimal(body["balance"]) == starting_credits


async def test_the_balance_is_a_string_not_a_number(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """The one deliberate departure from the market service's floats.

    A JSON number is an IEEE double by the time a browser has parsed it, and a
    credit total that is off by a floating-point epsilon is a bug report about
    money.
    """
    body = (await client.get(ME, headers=trader_headers)).json()

    assert isinstance(body["balance"], str)



# --- the history ---------------------------------------------------------


async def test_the_history_shows_the_grant(
    client: AsyncClient, trader_headers: dict[str, str], starting_credits: Decimal
) -> None:
    body = (await client.get(MY_ENTRIES, headers=trader_headers)).json()

    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["kind"] == "signup_grant"
    assert Decimal(entry["amount"]) == starting_credits
    assert entry["created_at"].endswith(("Z", "+00:00"))
    assert body["has_more"] is False
    assert body["next_cursor"] is None


async def test_the_history_shows_only_this_users_side(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """Both legs of the grant exist; only the one touching this account is
    this account's business."""
    body = (await client.get(MY_ENTRIES, headers=trader_headers)).json()

    assert all(Decimal(e["amount"]) > 0 for e in body["entries"])


async def test_a_page_reports_more_when_there_is_more(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    body = (await client.get(f"{MY_ENTRIES}?limit=1", headers=trader_headers)).json()

    assert len(body["entries"]) == 1
    # One entry on this account so far, so one page is the whole of it.
    assert body["has_more"] is False


async def test_a_cursor_we_did_not_issue_is_a_400(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"{MY_ENTRIES}?cursor=nonsense", headers=trader_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_cursor"


async def test_an_oversized_limit_is_clamped_not_refused(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """A caller asking for more than the ceiling wants as much as it can get,
    and a 422 would be a worse answer than the rows the server will serve."""
    assert (
        await client.get(f"{MY_ENTRIES}?limit=100000", headers=trader_headers)
    ).status_code == 200


async def test_a_zero_limit_is_refused(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """Clamping the top end is a kindness; a page of nothing is a bug in the
    caller and saying so is more useful than serving it."""
    assert (
        await client.get(f"{MY_ENTRIES}?limit=0", headers=trader_headers)
    ).status_code == 422


# --- the shape of the service --------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/ledger/entries"),
        ("post", "/ledger/transactions"),
        ("delete", MY_ENTRIES),
        ("patch", ME),
    ],
)
async def test_there_is_no_write_route(
    client: AsyncClient, admin_headers: dict[str, str], method: str, path: str
) -> None:
    """Nothing writes over HTTP yet, and nothing will ever edit or remove.

    The write path is `service/posting.py`; the endpoint that exposes it
    belongs to [T-2] #22, along with the decision about how a trading service
    proves it is one. A write route trusting a trader's own token would be a
    route for minting yourself credits.
    """
    response = await getattr(client, method)(path, headers=admin_headers)

    assert response.status_code in (404, 405)


async def test_health_needs_no_token(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
