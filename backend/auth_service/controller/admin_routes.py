"""Administrative routes: one user acting on another. [4.4] #16

Kept apart from `routes.py`, which is a user acting on their own session. The
split is for /docs as much as for the code — that page is the contract Michelle
codes against ([FE][4.2] #60 lands here next), and "how do I log in" and "how
do I grant somebody administrative authority" should not be one list.

Thin, like its sibling: parse, delegate, return. The rules are in
`service/user_admin.py` and are tested there without HTTP.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from controller.dependencies import CurrentActor, DbSession
from core.config import get_settings
from model.schemas import RoleChangeRequest, RoleChangeResponse, UserOut
from service import user_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.patch(
    "/users/{user_id}/role",
    response_model=RoleChangeResponse,
    summary="Change a user's role",
    description=(
        "[4.4] #16. Moves a user between `trader` and `admin`, and appends the "
        "decision to the shared audit log in the same transaction.\n\n"
        "There is no tier above `admin`: any administrator may promote or "
        "demote any other user, and the control against misuse is that they "
        "cannot do it unobserved. See ADR 0007.\n\n"
        "An administrator cannot change their own role. That single rule is "
        "what guarantees at least one administrator always remains.\n\n"
        "**The change does not reach other services immediately.** Authority "
        "travels in the access token, so the market service keeps honouring "
        "whatever the target's current token says until it expires — at most "
        "`takes_effect_within_seconds`. The target picks the new role up on "
        "their next token, which a client refresh supplies just as well as a "
        "fresh login. This service's own routes read the row and are current "
        "at once."
    ),
    responses={
        403: {
            "description": (
                "Not an administrator, or an administrator targeting themselves."
            )
        },
        404: {"description": "No user with that id."},
        409: {
            "description": (
                "Demoting this user would leave no administrator. Only "
                "reachable when two administrators demote each other at the "
                "same instant: sequentially the caller is themselves an "
                "administrator and cannot be the target, so a second one "
                "always remains."
            )
        },
    },
)
async def change_role(
    user_id: uuid.UUID,
    payload: RoleChangeRequest,
    actor: CurrentActor,
    session: DbSession,
) -> RoleChangeResponse:
    user = await user_admin.change_role(
        session,
        actor=actor,
        target_id=user_id,
        role=payload.role,
        reason=payload.reason,
    )
    return RoleChangeResponse(
        user=UserOut.model_validate(user),
        takes_effect_within_seconds=get_settings().access_token_ttl_seconds,
    )
