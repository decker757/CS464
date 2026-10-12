"""The ledger's side of auth's `POST /internal/users/lookup`. [L-1] #38, ADR 0021.

ADR 0021 section 2 and `docs/api/auth-service.md`: the request carries
`X-Service-Key` and `{"user_ids": [...]}`, 1 to 500 ids, "the ledger sends
more in chunks of 500"; the answer is one row of `id`, `username` and
`is_suspended` per id auth knows. "Any failure means nothing is written: ...
an auth error, a timeout, or a non-200 answer" (DECISIONS.md, "The refresh
runs every 60 seconds in a loop on each replica, behind
`LEADERBOARD_REFRESH_ENABLED`, and a failed refresh writes nothing"), so every
failure raises `AuthUnavailable` and none returns an empty answer.

The stand-in auth service is `leaderboard_fixtures.Directory`, under the
production client.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from unit_test.leaderboard_fixtures import (
    LOOKUP_LIMIT,
    LOOKUP_PATH,
    Directory,
    list_users,
    service_key,
)
from unit_test.trade_fixtures import errors


async def test_the_lookup_sends_the_service_key_and_no_token() -> None:
    """ADR 0021: "A user's token does not open the route". The key is the
    configured one, in `X-Service-Key`, and the body is the documented one
    and nothing more, which auth would refuse. What comes back is auth's
    three fields, with an unknown id left out."""
    directory = Directory()
    named, suspended, unknown = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    directory.name(named, "michelle_l")
    directory.name(suspended, "suspended_user", suspended=True)

    users = await list_users([named, suspended, unknown], directory)

    [request] = directory.requests
    assert (request.method, request.url.path) == ("POST", LOOKUP_PATH)
    assert request.headers["X-Service-Key"] == service_key()
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {
        "user_ids": [str(named), str(suspended), str(unknown)]
    }
    assert sorted((u.id, u.username, u.is_suspended) for u in users) == sorted(
        [(named, "michelle_l", False), (suspended, "suspended_user", True)]
    )


async def test_501_ids_are_asked_for_as_500_then_1() -> None:
    """"1 to 500 ids; the ledger sends more in chunks of 500." Every id is
    asked about once, and every user comes back."""
    directory = Directory()
    ids = [uuid.uuid4() for _ in range(LOOKUP_LIMIT + 1)]
    for number, user_id in enumerate(ids):
        directory.name(user_id, f"user_{number}")

    users = await list_users(ids, directory)

    assert [len(chunk) for chunk in directory.asked] == [500, 1]
    asked = [user_id for chunk in directory.asked for user_id in chunk]
    assert sorted(asked) == sorted(ids), "an id was dropped or asked about twice"
    assert {user.id for user in users} == set(ids)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(503, text="Service Unavailable"),
        httpx.Response(500, text="Internal Server Error"),
        # auth without the route yet: FastAPI's own 404.
        httpx.Response(404, json={"detail": "Not Found"}),
        httpx.Response(
            401,
            json={
                "error": {
                    "code": "invalid_service_key",
                    "message": "A valid service key is required.",
                }
            },
        ),
        httpx.ConnectError("auth is down"),
        httpx.ReadTimeout("auth is slow"),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json={"rows": []}),
        httpx.Response(200, json={"users": [{"id": str(uuid.uuid4()), "username": "x_y"}]}),
    ],
    ids=[
        "503",
        "500",
        "404",
        "401",
        "connect-error",
        "timeout",
        "200-not-json",
        "200-without-users",
        "200-user-missing-a-field",
    ],
)
async def test_a_lookup_auth_does_not_answer_raises(
    answer: httpx.Response | Exception,
) -> None:
    """Never an empty answer: a refresh handed `[]` for a failure would rank
    every holder as unknown. A 404 is treated like every other non-200
    (ruled for PR 5). The malformed 200s fail closed the same way, as
    `account_status` does with an answer it cannot read."""
    directory = Directory()
    user_id = uuid.uuid4()
    directory.name(user_id, "michelle_l")
    directory.fail(answer)

    with pytest.raises(errors().AuthUnavailable):
        await list_users([user_id], directory)


async def test_a_failure_on_a_later_chunk_raises() -> None:
    """The first chunk's users are not returned on their own: that would be a
    ranking without the second chunk's holders."""
    directory = Directory()
    ids = [uuid.uuid4() for _ in range(LOOKUP_LIMIT + 1)]
    for number, user_id in enumerate(ids):
        directory.name(user_id, f"user_{number}")
    directory.fail(httpx.Response(503, text="Service Unavailable"), on_request=2)

    with pytest.raises(errors().AuthUnavailable):
        await list_users(ids, directory)

    assert len(directory.asked) == 2
