"""Service-to-service routes: no browser calls these. [L-1] #38, ADR 0021

Apart from `routes.py` and `admin_routes.py` because the credential differs: a
key that a service holds, not a token that a user does. Rules live in
`service/user_directory.py`.
"""

from __future__ import annotations

from fastapi import APIRouter

from controller.dependencies import DbSession, ServiceKeyChecked
from model.schemas import DirectoryUserOut, UserLookupIn, UserLookupOut
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
        "An access token does not open this route, and the key opens no "
        "other. Reading is not an audited action."
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
