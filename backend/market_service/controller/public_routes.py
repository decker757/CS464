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
from datetime import UTC, datetime
from typing import Literal

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

# The `status` filter's accepted values, which are not all of `MarketStatus`.
#
# Typing the parameter as the enum itself puts all six members into the
# generated OpenAPI schema, so `/docs` advertises `draft` and `submitted` as
# choices and FastAPI accepts them. `service/browsing.py::_visible` then
# correctly refuses to show either, and the request comes back `200 []` — a
# filter that a frontend renders as a tab and that is permanently, silently
# empty. The route's own `responses={422: ...}` block and
# `docs/api/market-service.md` both promise a 422 there instead.
#
# Spelled as a `Literal` of the four public values rather than a second enum,
# so the generated schema carries exactly the list the contract states, and
# an unpublished market is refused at the request boundary rather than
# filtered out behind it. `_visible()` stays as it is: this narrows what may
# be *asked for*, and that one is what a trader may *see*. They are different
# rules and the visibility one must not depend on this.
PublicStatusFilter = Literal["open", "closed", "pending_resolution", "approved"]


@router.get(
    "",
    response_model=PublicMarketListResponse,
    summary="Browse published markets",
    description=(
        "[X-1] #34, [X-2] #35. With no query parameters, the default view: "
        "**every published market**, the ones still trading first and then "
        "by soonest closing time within each group. It is not an open-only "
        "list — [X-1] #34 asks that open markets be clearly distinguishable "
        "from closed, pending-resolution and settled ones, and there is "
        "nothing to distinguish them from if those are missing.\n\n"
        "`status` narrows to one of `open`, `closed`, `pending_resolution` "
        "or `approved`; `status=open` is the narrower query a trader gets by "
        "choosing the first group explicitly. `draft` and `submitted` are "
        "never visible here and are not accepted values. `q` searches the "
        "question, case-insensitively, and composes with `status`.\n\n"
        "A market past its `close_time` is reported `closed` even before the "
        "background sweep writes it down (ADR 0011), so `status=open` never "
        "includes one and `status=closed` does. In the default view it is "
        "present, labelled `closed`, and sorted behind whatever is still "
        "trading."
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
    status: PublicStatusFilter | None = Query(
        default=None,
        description=(
            "Restrict to one status. Omit for the default view, which is "
            "every published market with the ones still trading first."
        ),
    ),
) -> PublicMarketListResponse:
    # One clock for the whole request, read here because this is the only
    # place that sees both the query and the serialisation. D-025.
    now = datetime.now(UTC)

    markets = await browsing.browse(
        session, query=q, status=MarketStatus(status) if status else None, now=now
    )
    return PublicMarketListResponse(
        markets=[
            PublicMarketSummaryOut.model_validate(m, context={"now": now})
            for m in markets
        ]
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
    now = datetime.now(UTC)

    market = await browsing.get_published(session, market_id)
    return PublicMarketOut.model_validate(market, context={"now": now})
