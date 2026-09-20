"""Public HTTP routes for the trader-facing market read. [BE][X] #62.

Thin by design, like `controller/routes.py`: parse, delegate to
`service/browsing.py`, choose a status code. The one thing worth stating here
is who may call these — every route on `controller/routes.py` requires
`CurrentAdmin`, and this router is the reason it does not have to widen that
to serve a trader. These depend on `CurrentUser` instead: any valid access
token, any role (D-018). Not admin-gated, and not anonymous — every other
read in this backend authenticates a person from a signed token, and nothing
in [X-1] #34, [X-2] #35 or [X-3] #36 says a trader's browse page should be
the first exception.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from controller.dependencies import CurrentUser, DbSession
from model.entities import MarketStatus
from model.schemas import (
    PublicMarketListResponse,
    PublicMarketOut,
    PublicMarketSummaryOut,
)
from service import browsing

router = APIRouter(prefix="/public/markets", tags=["public markets"])


@router.get(
    "",
    response_model=PublicMarketListResponse,
    summary="Browse published markets",
    description=(
        "[X-1] #34, [X-2] #35. With no query parameters, the default view: "
        "open markets ordered by soonest closing time. `status` narrows to "
        "one of `open`, `closed`, `pending_resolution` or `approved` — "
        "`draft` and `submitted` are never visible here, whatever is asked "
        "for. `q` searches the question, case-insensitively, and composes "
        "with `status`.\n\n"
        "A market past its `close_time` is reported `closed` even before the "
        "background sweep writes it down (ADR 0011), so `status=open` and "
        "the default view never include one, and `status=closed` does."
    ),
    responses={422: {"description": "`status` is not one of the values above."}},
)
async def browse_markets(
    user: CurrentUser,
    session: DbSession,
    q: str | None = Query(
        default=None,
        alias="q",
        description="Case-insensitive containment search over the question.",
    ),
    status: MarketStatus | None = Query(
        default=None,
        description="Restrict to one status. Omit for the default open view.",
    ),
) -> PublicMarketListResponse:
    markets = await browsing.browse(session, query=q, status=status)
    return PublicMarketListResponse(
        markets=[PublicMarketSummaryOut.model_validate(m) for m in markets]
    )


@router.get(
    "/{market_id}",
    response_model=PublicMarketOut,
    summary="Read one published market",
    description=(
        "[X-3] #36. Unscoped — a published market belongs to every trader, "
        "not to whoever created it. Refuses a draft or a submitted market "
        "with the same `404 market_not_found` an unknown id gets, so the "
        "three are indistinguishable from outside.\n\n"
        "`status` is derived the same way the browse list derives it: a "
        "market past its `close_time` reads `closed` even before the sweep "
        "writes it (ADR 0011)."
    ),
    responses={404: {"description": "No such market, or it is a draft or submitted."}},
)
async def get_public_market(
    market_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> PublicMarketOut:
    market = await browsing.get_published(session, market_id)
    return PublicMarketOut.model_validate(market)
