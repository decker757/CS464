"""HTTP routes for the audit service: one route, and it reads. [4.3] #15

No write route by design. Writers append in their own transaction, and the
grants and trigger refuse any edit even if a route tried. ADR 0006.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from controller.dependencies import CurrentAdmin, DbSession
from core.config import get_settings
from model.schemas import AdminActionListResponse, AdminActionOut
from service import audit_service
from service.audit_service import ActionFilter

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get(
    "/actions",
    response_model=AdminActionListResponse,
    summary="Read the admin action log",
    description=(
        "[4.3] #15. Every administrative action across every service, newest "
        "first, filterable by actor and action type.\n\n"
        "Each entry is a snapshot taken when the action happened: the "
        "username and role are who the actor *was*, not who they are now, and "
        "no field is resolved at read time.\n\n"
        "Paged by keyset rather than offset, so entries appended while you "
        "read do not shift the pages under you. Send back `next_cursor` "
        "unmodified to continue; a null one means you have reached the end."
    ),
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
    },
)
async def list_actions(
    admin: CurrentAdmin,
    session: DbSession,
    actor_id: Annotated[
        uuid.UUID | None,
        Query(description="Only actions taken by this account."),
    ] = None,
    action_type: Annotated[
        str | None,
        Query(
            description="Exact match, for example `market.submitted`.",
            examples=["market.submitted"],
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
                "Entries per page. Defaults to the server's page size and is "
                "capped by it, because the log only grows."
            ),
        ),
    ] = None,
) -> AdminActionListResponse:
    settings = get_settings()
    # Clamped, not refused: a caller asking for more than the ceiling wants as
    # much as it can get.
    page_size = min(limit or settings.default_page_size, settings.max_page_size)

    page = await audit_service.list_actions(
        session,
        filters=ActionFilter(actor_id=actor_id, action_type=action_type),
        limit=page_size,
        cursor=cursor,
    )

    return AdminActionListResponse(
        actions=[AdminActionOut.model_validate(a) for a in page.actions],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )
