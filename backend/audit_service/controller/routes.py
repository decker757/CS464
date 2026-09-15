"""HTTP routes for the audit service.

One route, and it reads. There is deliberately no POST, PATCH or DELETE here:
[4.3] #15's second acceptance criterion is that the log is append-only with no
edit or delete path exposed, and an entry is appended by the service that
performed the action, inside that action's own transaction.

That is not enforced by this file being short. `audit_svc` holds no UPDATE or
DELETE grant, and a statement-level trigger in sql/02-schemas.sql refuses both
even for the table's owner, so a route added here in a hurry would fail against
the database rather than quietly working.
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
    # Clamped rather than rejected. A caller asking for more than the ceiling
    # wants as much as it can get, and a 422 on `limit=1000` would be a worse
    # answer than the 200 rows the server is willing to serve.
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
