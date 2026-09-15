"""HTTP routes for the market service.

Thin by design: parse, delegate to the service layer, choose a status code. No
business rule lives here, and no route builds an error response by hand. Domain
errors raised below the controller are turned into JSON by
`errors.register_error_handlers`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from controller.dependencies import CurrentActor, CurrentAdmin, DbSession
from model.schemas import (
    MarketDraftRequest,
    MarketListResponse,
    MarketOut,
    MarketSaveResponse,
    MarketSummaryOut,
    ValidationProblemOut,
)
from service import market_service

# Every route below depends on CurrentAdmin, so a trader's valid token gets a
# 403 rather than an empty list. There is no unauthenticated read here at all:
# the public market API is [BE][X] #62 and belongs to a different reader.
router = APIRouter(prefix="/markets", tags=["markets"])


@router.post(
    "",
    response_model=MarketSaveResponse,
    status_code=status.HTTP_200_OK,
    summary="Save a market as a draft, or submit it",
    description=(
        "[1.1] #1. One endpoint for both the form's three-second autosave "
        "(`status: draft`) and its submit button (`status: submitted`).\n\n"
        "Idempotent on `draft_key`: send the same client-generated UUID on "
        "every save for one form and this updates a single market. 201 the "
        "first time, 200 after that.\n\n"
        "A draft is never rejected for being incomplete, but the response "
        "always reports what would block submission. A submission is refused "
        "with 422 unless that list is empty, and writes nothing when refused.\n\n"
        "A successful submission appends an entry to the audit log ([4.3] "
        "#15), in the same transaction, so the two cannot disagree. An "
        "autosave does not."
    ),
    responses={
        201: {"description": "A market was created for this draft_key."},
        403: {"description": "Authenticated, but not an administrator."},
        409: {"description": "An autosave arrived for an already-submitted market."},
        422: {"description": "Submission refused; `error.details` lists every problem."},
    },
)
async def save_market(
    payload: MarketDraftRequest,
    actor: CurrentActor,
    session: DbSession,
    response: Response,
) -> MarketSaveResponse:
    market, problems, created = await market_service.save(session, actor, payload)

    if created:
        response.status_code = status.HTTP_201_CREATED

    return MarketSaveResponse(
        market=MarketOut.model_validate(market),
        blocking_submission=[
            ValidationProblemOut(field=p.field, message=p.message) for p in problems
        ],
    )


@router.get(
    "",
    response_model=MarketListResponse,
    summary="List my own markets",
    description=(
        "[1.1] #1. Scoped to the calling administrator. Another admin's drafts "
        "are not here, and neither are anyone else's."
    ),
)
async def list_markets(admin: CurrentAdmin, session: DbSession) -> MarketListResponse:
    markets = await market_service.list_for_creator(session, admin.user_id)
    return MarketListResponse(
        markets=[MarketSummaryOut.model_validate(m) for m in markets]
    )


@router.get(
    "/{market_id}",
    response_model=MarketOut,
    summary="Read one of my own markets",
    description=(
        "[1.1] #1. Reloads a draft after a page refresh. Returns 404 for a "
        "market belonging to another administrator, not 403: a 403 would "
        "confirm that market exists, which is most of what the draft "
        "visibility rule is meant to hide."
    ),
    responses={404: {"description": "No such market, or it is not yours."}},
)
async def get_market(
    market_id: uuid.UUID, admin: CurrentAdmin, session: DbSession
) -> MarketOut:
    market = await market_service.get(session, admin.user_id, market_id)
    return MarketOut.model_validate(market)
