"""Service-to-service routes: no browser calls these. [L-1] #38, ADR 0021, #640

Apart from `routes.py` and `admin_routes.py` because the credential differs: a
key that a service holds, not a token that a user does. Rules live in
`service/user_directory.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from controller.dependencies import DbSession, ServiceKeyChecked
from model.schemas import (
    DirectoryUserOut,
    SuspendedUsersOut,
    UserLookupIn,
    UserLookupOut,
)
from service import user_directory

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post(
    "/users/lookup",
    response_model=UserLookupOut,
    summary="Name accounts for another service",
    description=(
        "[L-1] #38. **Service-to-service; not for the frontend.** The "
        "ledger's leaderboard refresh turns account ids into usernames here. "
        "A browser cannot call it, because it needs `X-Service-Key`, which no "
        "browser holds. ADR 0021.\n\n"
        "Send 1 to 500 ids; a duplicate is answered once and an id that names "
        "no user is left out. Each user is exactly `id`, `username` and "
        "`is_suspended`, ordered by `id`.\n\n"
        "An access token does not open this route, and the key opens none "
        "outside `/internal`. Reading is not an audited action."
    ),
    responses={
        401: {"description": "`X-Service-Key` missing or incorrect."},
    },
)
async def lookup_users(
    body: UserLookupIn, session: DbSession, _: ServiceKeyChecked
) -> UserLookupOut:
    users = await user_directory.list_by_ids(session, body.user_ids)
    return UserLookupOut(
        users=[DirectoryUserOut.model_validate(user) for user in users]
    )


@router.get(
    "/users/suspended",
    response_model=SuspendedUsersOut,
    summary="List suspended accounts for another service",
    description=(
        "#640. **Service-to-service; not for the frontend.** The ledger's "
        "local copy of who is suspended refreshes from here, so a trade asks "
        "that copy rather than calling auth. A browser cannot call it, because "
        "it needs `X-Service-Key`, which no browser holds. ADR 0030, the "
        "ledger's local copy.\n\n"
        "`user_ids` is every suspended account's id, ordered by `id`; an "
        "active account is absent. `as_of` is taken just before the read, so "
        "the list is at least as fresh as it says.\n\n"
        "An access token does not open this route, and the key opens none "
        "outside `/internal`. Reading is not an audited action."
    ),
    responses={
        401: {"description": "`X-Service-Key` missing or incorrect."},
    },
)
async def list_suspended_users(
    session: DbSession, _: ServiceKeyChecked
) -> SuspendedUsersOut:
    as_of = datetime.now(UTC)
    user_ids = await user_directory.list_suspended_ids(session)
    return SuspendedUsersOut(as_of=as_of, user_ids=user_ids)
