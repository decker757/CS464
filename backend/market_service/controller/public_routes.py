"""HTTP routes for the trader-facing market read. [BE][X] #62.

Thin, like `controller/routes.py`. Any valid access token of any role may call
these; not admin-gated, and not anonymous. D-018.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError
from pydantic import AwareDatetime

from controller.dependencies import CurrentUser, DbSession, PageCursor, PageLimit
from core.paging import page_size_of
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

# The closing window's two ends. #442. With an offset, because a time without
# one is a different instant in every timezone. DECISIONS.md, "The browse
# narrows by a half-open window on when trading stops".
ClosesFrom = Annotated[
    AwareDatetime | None,
    Query(
        description=(
            "ISO-8601 time with an offset (`Z` or `+08:00`; send `+` as "
            "`%2B`). Markets that stopped trading, or will stop, at or "
            "after it."
        ),
    ),
]
ClosesBefore = Annotated[
    AwareDatetime | None,
    Query(
        description=(
            "ISO-8601 time with an offset. Markets that stopped trading, or "
            "will stop, strictly before it."
        ),
    ),
]


def _has_utc_instant(moment: datetime) -> bool:
    """Does `moment` name an instant inside year 1 to 9999 in UTC? #490.

    asyncpg converts every bound to UTC before it sends it, and a time near
    either end of `datetime`'s range can fall outside it there: the last hour
    of year 9999 at UTC-5, or year 1 at UTC+1. Only that is refused; every
    instant a `datetime` can hold in UTC still passes.
    """
    try:
        moment.astimezone(UTC)
    except OverflowError:
        return False
    return True


def _window_problem(parameter: str, value: datetime, message: str) -> dict:
    """One closing-window end's item in a `422`, shaped like FastAPI's own."""
    return {
        "type": "value_error",
        "loc": ("query", parameter),
        "msg": message,
        "input": value,
    }


def _refuse_if_window_unencodable(
    closes_from: datetime | None, closes_before: datetime | None
) -> None:
    """Raise `RequestValidationError` for an end with no instant in UTC, in
    the shape `_refuse_if_window_empty` uses, rather than letting asyncpg's
    overflow become a 500. #490. Kept here, not in `shared/`: this is the one
    caller that sends such a bound to Postgres (ADR 0012); the ledger's price
    history compares its bounds in Python."""
    problems = []
    for parameter, value in (
        ("closes_from", closes_from),
        ("closes_before", closes_before),
    ):
        if value is not None and not _has_utc_instant(value):
            message = f"`{parameter}` has no instant between year 1 and 9999 in UTC."
            problems.append(_window_problem(parameter, value, message))
    if problems:
        raise RequestValidationError(problems)


def _refuse_if_window_empty(
    closes_from: datetime | None, closes_before: datetime | None
) -> None:
    """Raise `RequestValidationError` for a window holding no instant, so
    FastAPI answers it as the `422` every other bad parameter gets, with an
    item for each end. The twin of the ledger's `_refuse_if_window_reversed`,
    whose window is closed at both ends and so may be a single instant."""
    if closes_from is None or closes_before is None or closes_from < closes_before:
        return

    message = "`closes_before` must be later than `closes_from`."
    raise RequestValidationError(
        [
            _window_problem("closes_from", closes_from, message),
            _window_problem("closes_before", closes_before, message),
        ]
    )


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
        "`status` narrows to one of `open`, `closed`, `pending_resolution`, "
        "`approved`, `settled` or `voided`; `status=open` is the narrower "
        "query a trader gets by choosing the first group explicitly. `draft` and "
        "`submitted` are never visible here and are not accepted values. `q` "
        "searches the question, case-insensitively, and composes with "
        "`status`.\n\n"
        "`closes_from` and `closes_before` narrow to markets that stopped "
        "trading, or will stop, at or after the first and strictly before "
        "the second, so consecutive windows never share a market. That is "
        "the same moment the stopped markets sort by: `close_time` for a "
        "market still trading or closed by the clock, and the moment of the "
        "close for one closed early. Either may be sent alone; both compose "
        "with `status` and `q`. Each is a time with an offset. A market with "
        "no `close_time` that has not been closed matches no window.\n\n"
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
        "with the same `q`, `status`, closing window and `limit`, to "
        "continue; null means "
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
                "than a question may be or contains a NUL character, "
                "`closes_from` or `closes_before` is not a time with an "
                "offset or has no instant between year 1 and 9999 in UTC, "
                "`closes_before` is not later than `closes_from`, or "
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
    closes_from: ClosesFrom = None,
    closes_before: ClosesBefore = None,
    limit: PageLimit = None,
    cursor: PageCursor = None,
) -> PublicMarketListResponse:
    _refuse_if_window_unencodable(closes_from, closes_before)
    _refuse_if_window_empty(closes_from, closes_before)

    # One clock for the request, so filtering and the displayed status agree.
    # D-025, D-027.
    now = datetime.now(UTC)

    page = await browsing.browse(
        session,
        limit=page_size_of(limit),
        query=q,
        status=MarketStatus(status) if status else None,
        closes_from=closes_from,
        closes_before=closes_before,
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
        "writes it (ADR 0011).\n\n"
        "`settleable` ([3.4] #12) is true from five minutes after the dispute "
        "window ends and stays true once the market is `settled`; false for "
        "every other status, `voided` included.\n\n"
        "A `voided` market ([BE] #221) has no winning outcome: "
        "`proposed_outcome_id` is null, even on one voided after its outcome "
        "was approved."
    ),
    responses={404: {"description": "No such market, or it is a draft or submitted."}},
)
async def get_public_market(
    market_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> PublicMarketOut:
    now = datetime.now(UTC)

    market = await browsing.get_published(session, market_id, now=now)
    return PublicMarketOut.model_validate(market)
