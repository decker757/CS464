"""Administrative routes: one user acting on another. [4.4] #16, [4.1] #13, [4.2] #14, [2.4] #273

Apart from `routes.py` (a user's own session) so /docs lists them separately.
The account search and lookup are here too, though they change nothing,
because the guard and the audience are the same. Rules live in
`service/user_admin.py` and `service/user_directory.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from controller.dependencies import CurrentActor, CurrentAdmin, DbSession
from core.config import get_settings
from core.roles import UserRole
from model.schemas import (
    AdminUserLookupIn,
    AdminUserLookupOut,
    AdminUserOut,
    RoleChangeRequest,
    RoleChangeResponse,
    SuspensionRequest,
    SuspensionResponse,
    UserListResponse,
    UserNameOut,
    UserOut,
)
from service import user_admin, user_directory

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
        "`role` and `is_suspended` narrow the list to administrators or "
        "traders, and to suspended or active accounts. Every condition given "
        "must hold, `q` included. An unknown `role` is a 422.\n\n"
        "The ledger lives on another service and another port: take `id` from "
        "here to `GET /ledger/users/{user_id}/entries` for the transaction "
        "history, and `GET /ledger/users/{user_id}/balance` for the balance. "
        "This service does not know that credits exist (ADR 0003).\n\n"
        "Paged by keyset rather than offset, so accounts registered while you "
        "read do not shift the pages under you. Send back `next_cursor` "
        "unmodified to continue, with the same `q`, `role` and `is_suspended`; "
        "a null one means you have reached the end.\n\n"
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
    role: Annotated[
        UserRole | None,
        Query(description="Only accounts holding this role. Omit for both."),
    ] = None,
    is_suspended: Annotated[
        bool | None,
        Query(description="Only suspended accounts, or only active ones. Omit for both."),
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
    # Clamped, not refused, like the audit feed and the ledger history: a
    # caller asking for more than the ceiling wants as much as it can get.
    page_size = min(limit or settings.default_page_size, settings.max_page_size)

    page = await user_admin.search_users(
        session,
        query=q,
        role=role,
        is_suspended=is_suspended,
        limit=page_size,
        cursor=cursor,
    )

    return UserListResponse(
        users=[AdminUserOut.model_validate(user) for user in page.users],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.post(
    "/users/lookup",
    response_model=AdminUserLookupOut,
    summary="Name accounts for a trade table",
    description=(
        "[2.4] #273. Turns the `user_id`s on a page of the ledger's "
        "`GET /ledger/markets/{id}/trades` into usernames, in one request. "
        "The ledger holds no user table (ADR 0003), so the admin's browser "
        "joins the names in.\n\n"
        "Send 1 to 200 ids, one trades page's worth. An id that names no user "
        "is left out, and a duplicate is answered once. No order is promised: "
        "key the answer by `id`.\n\n"
        "Suspended users are included, since a suspended trader is the one an "
        "administrator is most likely investigating. Each user is exactly `id` "
        "and `username`; `is_suspended` stays on `GET /admin/users`.\n\n"
        "Reading is not an audited action, like the search."
    ),
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
    },
)
async def lookup_users(
    body: AdminUserLookupIn, admin: CurrentAdmin, session: DbSession
) -> AdminUserLookupOut:
    users = await user_directory.list_by_ids(session, body.user_ids)
    return AdminUserLookupOut(
        users=[UserNameOut.model_validate(user) for user in users]
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


@router.patch(
    "/users/{user_id}/suspension",
    response_model=SuspensionResponse,
    summary="Suspend or unsuspend an account",
    description=(
        "[4.2] #14. Sets whether an account may log in and trade, with a "
        "reason, and appends `user.suspended` or `user.unsuspended` to the "
        "shared audit log in the same transaction.\n\n"
        "`suspended` is the state the account should end up in, so a repeated "
        "request is the same outcome and records nothing. `reason` is "
        "required both ways and must be at least ten characters once "
        "trimmed; it is kept only in the audit log.\n\n"
        "Suspending ends every session the account has, and unsuspending "
        "brings none of them back. Login is refused at once, and so is "
        "trading: the ledger asks this service on every trade (ADR 0022). "
        "**Reads elsewhere do not stop at once.** The other services "
        "authorise from the access token, so they honour the account's "
        "current one for up to `takes_effect_within_seconds`. Positions are "
        "untouched."
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
                "Suspending this administrator would leave none who can log "
                "in. Only reachable when two administrators act on each other "
                "at the same instant."
            )
        },
        422: {
            "description": (
                "`suspension_incomplete`: the reason is blank or shorter than "
                "ten characters once trimmed. `details` names `reason`."
            )
        },
    },
)
async def set_suspension(
    user_id: uuid.UUID,
    payload: SuspensionRequest,
    actor: CurrentActor,
    session: DbSession,
) -> SuspensionResponse:
    user = await user_admin.set_suspension(
        session,
        actor=actor,
        target_id=user_id,
        suspended=payload.suspended,
        reason=payload.reason,
    )
    return SuspensionResponse(
        user=AdminUserOut.model_validate(user),
        takes_effect_within_seconds=get_settings().access_token_ttl_seconds,
    )
