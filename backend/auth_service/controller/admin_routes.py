"""Administrative routes: one user acting on another. [4.4] #16, [4.1] #13

Kept apart from `routes.py`, which is a user acting on their own session. The
split is for /docs as much as for the code — that page is the contract Michelle
codes against ([FE][4.1] #57 and [FE][4.2] #60 land here next), and "how do I
log in" and "how do I grant somebody administrative authority" should not be
one list.

Reading is here too, and it is the odd one out: searching accounts changes
nothing and is not audited. It belongs beside the routes that do change things
because the guard and the audience are the same, and because the frontend
reaches one of these pages by way of the other.

Thin, like its sibling: parse, delegate, return. The rules are in
`service/user_admin.py` and are tested there without HTTP.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from controller.dependencies import CurrentActor, CurrentAdmin, DbSession
from core.config import get_settings
from model.schemas import (
    AdminUserOut,
    RoleChangeRequest,
    RoleChangeResponse,
    UserListResponse,
    UserOut,
)
from service import user_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="Search user accounts",
    description=(
        "[4.1] #13. Accounts an administrator can investigate, newest "
        "registration first.\n\n"
        "`q` matches a fragment of either the username or the email, "
        "case-insensitively, because somebody chasing an anomaly has part of a "
        "name rather than the whole of one. Omit it to list everybody.\n\n"
        "The ledger lives on another service and another port: take `id` from "
        "here to `GET /ledger/users/{user_id}/entries` for the transaction "
        "history, and `GET /ledger/users/{user_id}/balance` for the balance. "
        "This service does not know that credits exist (ADR 0003).\n\n"
        "Paged by keyset rather than offset, so accounts registered while you "
        "read do not shift the pages under you. Send back `next_cursor` "
        "unmodified to continue; a null one means you have reached the end.\n\n"
        "Reading is not an audited action. The log records decisions — "
        "promotions, suspensions — and an entry per search would bury them."
    ),
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
    },
)
async def list_users(
    admin: CurrentAdmin,
    session: DbSession,
    q: Annotated[
        str | None,
        Query(
            max_length=320,
            description=(
                "Part of a username or an email address. Matched as literal "
                "text: `%` and `_` are not wildcards."
            ),
            examples=["ernest"],
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(description="The `next_cursor` from the previous page."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            description=(
                "Accounts per page. Defaults to the server's page size and is "
                "capped by it, because the user table only grows."
            ),
        ),
    ] = None,
) -> UserListResponse:
    settings = get_settings()
    # Clamped rather than rejected, like the audit feed and the ledger history.
    # A caller asking for more than the ceiling wants as much as it can get,
    # and a 422 on `limit=1000` would be a worse answer than the 200 rows the
    # server is willing to serve.
    page_size = min(limit or settings.default_page_size, settings.max_page_size)

    page = await user_admin.search_users(
        session, query=q, limit=page_size, cursor=cursor
    )

    return UserListResponse(
        users=[AdminUserOut.model_validate(user) for user in page.users],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


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
