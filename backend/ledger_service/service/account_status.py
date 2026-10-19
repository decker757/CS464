"""May this trader still trade? [4.2] #14, ADR 0022.

The ledger authorises from the signed token alone, so a suspended account
could trade until its access token expired. The trade path closes that by
asking auth's `GET /auth/me` with the trader's own token, once per trade that
is not a replay, before the market gate. auth answers `403 account_suspended`
for a suspended account; anything unclear fails closed.

The client is held for the process (`main.py`), and `open_client` takes a
transport so the suite drives the real request through `httpx.MockTransport`.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.database import release_before_calling_out
from core.errors import AccountSuspended, AuthUnavailable, NotAuthenticated

logger = logging.getLogger(__name__)

# Stated rather than inherited, as the terms client's is (D-030). Its own
# constant: the two dependencies' budgets may reasonably diverge.
_TIMEOUT = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)

# auth_service's `AccountSuspended.code`, restated: this service cannot import
# auth's. auth's `test_a_suspended_user_s_bearer_token_gets_403_from_me` pins
# its side of the contract.
_SUSPENDED = "account_suspended"


def _is_suspension(response: httpx.Response) -> bool:
    """Whether a 403 is auth's `account_suspended`, and not some other 403.

    A body that does not decode, or is not the error envelope, is not one.
    """
    try:
        body = response.json()
    except (ValueError, RecursionError):
        return False
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return False
    return body["error"].get("code") == _SUSPENDED


def open_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The client every account check goes through: auth's base URL and
    D-030's timeout. The caller closes it.

    `main.py`'s lifespan opens one for the process. `transport` is the
    suite's seam.
    """
    return httpx.AsyncClient(
        base_url=get_settings().auth_service_url,
        transport=transport,
        timeout=_TIMEOUT,
    )


async def refuse_if_suspended(
    session: AsyncSession,
    *,
    access_token: str,
    auth_client: httpx.AsyncClient,
) -> None:
    """Raise unless auth says the token's owner may act. Otherwise return `None`.

    | auth answers | raised |
    | --- | --- |
    | `200` | nothing |
    | `403` with code `account_suspended` | `AccountSuspended` |
    | `401` | `NotAuthenticated`, and a WARNING |
    | anything else, or no answer | `AuthUnavailable` |

    **Call this with nothing pending and no row lock held**: it rolls back
    before the call, so it holds no connection while auth answers (D-043).
    The `200` body is not read: auth answers `200` only after it has checked
    the account.
    """
    await release_before_calling_out(session, caller="refuse_if_suspended()")

    try:
        response = await auth_client.get(
            "/auth/me", headers={"Authorization": f"Bearer {access_token}"}
        )
    # `RequestError`, as the terms client: every failure under it means the
    # dependency did not answer.
    except httpx.RequestError as exc:
        raise AuthUnavailable from exc

    if response.status_code == 200:
        return
    if response.status_code == 401:
        # This service accepted the token before asking, so auth refusing it
        # means the two disagree about it: a signing key, an issuer or a
        # clock. Every request would then fail 401 with nothing else logged.
        logger.warning("auth answered 401 to a token this service accepted")
        raise NotAuthenticated
    if response.status_code == 403 and _is_suspension(response):
        raise AccountSuspended
    raise AuthUnavailable
