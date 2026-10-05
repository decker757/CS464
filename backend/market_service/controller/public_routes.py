"""HTTP routes for the trader-facing market read. [BE][X] #62.

Thin, like `controller/routes.py`. Any valid access token of any role may call
these; not admin-gated, and not anonymous. D-018.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query

from controller.dependencies import CurrentUser, DbSession
from core.config import get_settings
from model.entities import MarketStatus, PublicMarketStatus
from model.schemas import (
    MAX_QUESTION_LENGTH,
    PublicMarketListResponse,
    PublicMarketOut,
    PublicMarketSummaryOut,
)
from service import browsing

router = APIRouter(prefix="/public/markets", tags=["public markets"])

# The `status` filter's accepted values: only what a trader can see, so
# `draft` is a 422 rather than a silently empty `200 []` tab. Not
# `MarketStatus`, which would accept every member, and not a hand-written
# Literal, which would be a second copy of `_visible`'s set.
PublicStatusFilter = PublicMarketStatus


@router.get(
    "",
    response_model=PublicMarketListResponse,
    summary="Browse published markets",
    description=(
        "[X-1] #34, [X-2] #35. With no query parameters, the default view: "
        "**every published market**: the ones still trading first, soonest "
        "closing time first; then every other market, whatever its status, "
        "most recently stopped first — for a market closed early, the moment "
        "it was closed rather than its `close_time`. Ties come back in `id` "
        "order. The same order applies under `status` and `q`. It is not an "
        "open-only list — [X-1] #34 asks that open markets be clearly "
        "distinguishable from closed, pending-resolution and settled ones, "
        "and there is nothing to distinguish them from if those are "
        "missing.\n\n"
        "`status` narrows to one of `open`, `closed`, `pending_resolution` "
        "or `approved`; `status=open` is the narrower query a trader gets by "
        "choosing the first group explicitly. `draft` and `submitted` are "
        "never visible here and are not accepted values. `q` searches the "
        "question, case-insensitively, and composes with `status`.\n\n"
        "A market past its `close_time` is reported `closed` even before the "
        "background sweep writes it down (ADR 0011), so `status=open` never "
        "includes one and `status=closed` does. In the default view it is "
        "present, labelled `closed`, and sorted behind whatever is still "
        "trading.\n\n"
        "Each market carries its `outcomes` as `{id, position, label}`, "
        "ordered by `position` — the same shape the detail read returns — "
        "so a card can name every outcome without a detail fetch per row "
        "(#214). A market has two or more, with any labels; do not assume "
        "`Yes` and `No`.\n\n"
        "Paged by keyset ([X-1] #104). Send `next_cursor` back as `cursor`, "
        "with the same `q`, `status` and `limit`, to continue; null means "
        "there is nothing after this page. `limit` defaults to the server's "
        "page size (50) and a value above its maximum (200) is clamped. Pages "
        "read in turn never repeat a market: the split into trading and "
        "stopped is fixed at the first page's instant, which the cursor "
        "carries, while each card's `status` is read now."
    ),
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        422: {
            "description": (
                "`status` is not one of the values above, `q` is longer "
                "than a question may be or contains a NUL character, or "
                "`limit` is below 1."
            )
        },
    },
)
async def browse_markets(
    user: CurrentUser,
    session: DbSession,
    q: str | None = Query(
        default=None,
        alias="q",
        max_length=MAX_QUESTION_LENGTH,
        # No NUL: Postgres text cannot hold one, and asyncpg's error would be
        # a 500. Refused as a 422 rather than stripped, which would answer a
        # question the trader did not ask.
        pattern=r"^[^\x00]*$",
        description=(
            "Case-insensitive containment search over the question. "
            "Surrounding whitespace is ignored, and a blank search is the "
            "same as omitting it."
        ),
    ),
    status: PublicStatusFilter | None = Query(
        default=None,
        description=(
            "Restrict to one status. Omit for the default view, which is "
            "every published market with the ones still trading first."
        ),
    ),
    limit: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Markets per page. Defaults to the server's page size and is "
            "capped by its maximum: a larger value is clamped, not refused."
        ),
    ),
    cursor: str | None = Query(
        default=None,
        description="The `next_cursor` from the previous page, unmodified.",
    ),
) -> PublicMarketListResponse:
    # One clock for the request, so filtering and the displayed status agree.
    # D-025, D-027.
    now = datetime.now(UTC)

    settings = get_settings()
    # Clamped, not refused, as the audit feed and the user list do.
    page_size = min(limit or settings.default_page_size, settings.max_page_size)

    page = await browsing.browse(
        session,
        limit=page_size,
        query=q,
        status=MarketStatus(status) if status else None,
        now=now,
        cursor=cursor,
    )
    return PublicMarketListResponse(
        markets=[PublicMarketSummaryOut.model_validate(card) for card in page.markets],
        next_cursor=page.next_cursor,
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

    market = await browsing.get_published(session, market_id, now=now)
    return PublicMarketOut.model_validate(market)
