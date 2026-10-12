"""Naming the leaderboard's holders through auth's lookup. [L-1] #38, ADR 0021.

auth's `POST /internal/users/lookup` answers `id`, `username` and
`is_suspended` for every id it knows, to the ledger's `X-Service-Key` and to
nothing else. The client is the one `account_status.open_client` builds for
the process: DECISIONS.md, "One auth client in the ledger, for #14's trade
check and #38's leaderboard".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from core.config import get_settings
from core.errors import AuthUnavailable

_LOOKUP_PATH = "/internal/users/lookup"

# auth's maximum per request, restated from `docs/api/auth-service.md`: "between
# 1 and 500 UUIDs. The ledger sends larger sets in chunks of 500."
_CHUNK_SIZE = 500


@dataclass(frozen=True)
class DirectoryUser:
    """One user as auth's lookup names them."""

    id: uuid.UUID
    username: str
    is_suspended: bool


def _user_of(row: object) -> DirectoryUser:
    """One row of the lookup's answer. Raises `AuthUnavailable` for a row that
    is not the documented three fields."""
    if not isinstance(row, dict):
        raise AuthUnavailable
    raw_id = row.get("id")
    username = row.get("username")
    is_suspended = row.get("is_suspended")
    if not (
        isinstance(raw_id, str)
        and isinstance(username, str)
        and isinstance(is_suspended, bool)
    ):
        raise AuthUnavailable
    try:
        user_id = uuid.UUID(raw_id)
    except ValueError as exc:
        raise AuthUnavailable from exc
    return DirectoryUser(id=user_id, username=username, is_suspended=is_suspended)


def _users_of(response: httpx.Response) -> list[DirectoryUser]:
    """The users in a `200`. Raises `AuthUnavailable` for a body that is not
    JSON or has no `users` list, as `account_status` fails closed on an answer
    it cannot read."""
    try:
        body = response.json()
    except (ValueError, RecursionError) as exc:
        raise AuthUnavailable from exc
    if not isinstance(body, dict) or not isinstance(body.get("users"), list):
        raise AuthUnavailable
    return [_user_of(row) for row in body["users"]]


async def _list_one_chunk(
    user_ids: Sequence[uuid.UUID], auth_client: httpx.AsyncClient
) -> list[DirectoryUser]:
    """One request of at most `_CHUNK_SIZE` ids. Raises `AuthUnavailable` for
    no answer or any answer but a readable `200`."""
    try:
        response = await auth_client.post(
            _LOOKUP_PATH,
            headers={"X-Service-Key": get_settings().ledger_service_key},
            json={"user_ids": [str(user_id) for user_id in user_ids]},
        )
    # `RequestError`, as the account check: every failure under it means auth
    # did not answer.
    except httpx.RequestError as exc:
        raise AuthUnavailable from exc
    if response.status_code != 200:
        raise AuthUnavailable
    return _users_of(response)


async def list_users(
    user_ids: Sequence[uuid.UUID], auth_client: httpx.AsyncClient
) -> list[DirectoryUser]:
    """The users auth knows among `user_ids`, asked for in chunks of 500, in
    order. An id auth does not know is left out.

    Raises `AuthUnavailable` if any chunk fails, and returns nothing gathered
    before it: a partial list would rank only some holders. Sends no request
    for an empty `user_ids`. Holds no database connection; the caller ends its
    transaction first (ADR 0021).
    """
    users: list[DirectoryUser] = []
    for start in range(0, len(user_ids), _CHUNK_SIZE):
        users.extend(await _list_one_chunk(user_ids[start : start + _CHUNK_SIZE], auth_client))
    return users
