"""HTTP routes for the ledger service.

Thin by design: parse, delegate to the service layer, choose a status code. No
business rule lives here, and no route builds an error response by hand. Domain
errors raised below the controller are turned into JSON by
`errors.register_error_handlers`.

Nothing edits or removes a ledger entry, ever, and there is no PUT, PATCH or
DELETE anywhere on this service. That is the whole design, it is enforced by
a trigger rather than by this file being short, and a route added here in a
hurry would fail against the database.

**`POST /markets/{market_id}/trades` is this service's first write route.**
[T-2] #22 answers ADR 0009's deferred service-auth question for exactly this
shape of caller: the route takes no account, no amount and no leg — the
request model is `extra="forbid"` over five fields, none of them money — so
the debited account is `accounts.ensure(USER, claims.sub)` rather than
anything in the body, and a trader's own token is safe to accept because
there is nothing in the request for it to mint. See ADR 0009's amendment.

The grant is the older exception that proves the same rule from the read
side: reading a balance can write, because [B-1] #32's starting credits are
minted lazily on first read. See `service/grants.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

# DbSession is the injected annotation and belongs on route signatures only.
# The two private helpers below take a plain AsyncSession: FastAPI never looks
# at them, so an Annotated[..., Depends(...)] there would advertise an
# injection that does not happen.
from controller.dependencies import (
    AccessToken,
    CurrentAdmin,
    CurrentUser,
    DbSession,
    RedisClient,
)
from core.config import get_settings
from core.pricing import Side
from model.schemas import (
    BalanceOut,
    LedgerEntryListResponse,
    LedgerEntryOut,
    OutcomePriceOut,
    PreviewOut,
    SnapshotOut,
    TradeIn,
    TradeOut,
)
from service import (
    ledger_service,
    preview as preview_service,
    snapshot as snapshot_service,
    trading,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])

_BALANCE_DESCRIPTION = (
    "[B-2] #33. Available credits, derived by summing every entry on the "
    "account. There is no stored balance for this number to disagree with.\n\n"
    "Exact decimal strings, not JSON numbers. A balance that has been through "
    "an IEEE double is a balance that can be wrong by an epsilon, and this is "
    "money.\n\n"
    "The first call for a user also mints their starting credits ([B-1] #32), "
    "as a real append-only transaction, keyed on the user id so it can happen "
    "exactly once. Every later call just reads."
)

_ENTRIES_DESCRIPTION = (
    "[4.1] #13. The account's entries, newest first, each with the balance it "
    "left behind. Both sides of a movement are separate entries sharing one "
    "`transaction_id`, and only the side touching this account appears here.\n\n"
    "`balance_after` is derived per read, not stored, so the newest entry's "
    "value is the same number the balance route returns.\n\n"
    "Paged by keyset rather than offset, so entries appended while you read do "
    "not shift the pages under you. Send back `next_cursor` unmodified to "
    "continue; a null one means you have reached the end."
)


def _page_size(limit: int | None) -> int:
    """Clamped rather than rejected.

    A caller asking for more than the ceiling wants as much as it can get, and
    a 422 on `limit=1000` is a worse answer than the 200 rows the server is
    willing to serve. Same rule as the audit feed.
    """
    settings = get_settings()
    return min(limit or settings.default_page_size, settings.max_page_size)


PageLimit = Annotated[
    int | None,
    Query(
        ge=1,
        description=(
            "Entries per page. Defaults to the server's page size and is "
            "capped by it, because a ledger only grows."
        ),
    ),
]

PageCursor = Annotated[
    str | None,
    Query(description="The `next_cursor` from the previous page."),
]

PreviewQuantity = Annotated[
    Decimal,
    Query(
        gt=0,
        max_digits=18,
        decimal_places=4,
        description=(
            "Shares to trade. At most four decimal places (D-038) — a fifth "
            "is 422 rather than rounded, because rounding would quote a "
            "trade for a quantity the trader never typed. At most 18 digits "
            "in all, the width of `Numeric(18, 4)`: a quantity wider than "
            "the column could never be written as a share count."
        ),
    ),
]

_PREVIEW_DESCRIPTION = (
    "[T-1] #21. What a trade would cost right now, and how it would move "
    "every outcome's price, computed from `core/lmsr.py` — not estimated.\n\n"
    "Any valid access token, any role (D-018): a preview mints nothing and "
    "reveals nothing beyond the public market read.\n\n"
    "`total` is signed — negative on a buy, positive on a sell — and "
    "quantized by the same function [T-2] #22 uses to build its legs, so "
    "this is the number that would be charged. `state_version` is the quote "
    "reference (D-011); nothing else in the response is a second one.\n\n"
    "**The first request on a market writes.** A market nobody has touched "
    "yet has no book: this route opens and funds one, once per market ever "
    "(D-008, D-037), which can take up to the market-terms timeout. Every "
    "request after that is a single indexed read.\n\n"
    "Does not check whether the market is still open — the book carries no "
    "status. Gate on the market read's derived status instead."
)


@router.get(
    "/balances/me",
    response_model=BalanceOut,
    summary="My available credit balance",
    description=_BALANCE_DESCRIPTION,
    responses={401: {"description": "Missing, malformed or expired access token."}},
)
async def my_balance(user: CurrentUser, session: DbSession) -> BalanceOut:
    return await _balance(session, user.user_id)


@router.get(
    "/entries/me",
    response_model=LedgerEntryListResponse,
    summary="My ledger history",
    description=_ENTRIES_DESCRIPTION,
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        401: {"description": "Missing, malformed or expired access token."},
    },
)
async def my_entries(
    user: CurrentUser,
    session: DbSession,
    cursor: PageCursor = None,
    limit: PageLimit = None,
) -> LedgerEntryListResponse:
    return await _entries(session, user.user_id, cursor=cursor, limit=limit)


@router.get(
    "/users/{user_id}/balance",
    response_model=BalanceOut,
    summary="Any user's balance",
    description=(
        "[4.1] #13. The same number the user sees, for an administrator "
        "investigating an anomaly.\n\n" + _BALANCE_DESCRIPTION
    ),
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
    },
)
async def user_balance(
    user_id: uuid.UUID, admin: CurrentAdmin, session: DbSession
) -> BalanceOut:
    return await _balance(session, user_id)


@router.get(
    "/users/{user_id}/entries",
    response_model=LedgerEntryListResponse,
    summary="Any user's ledger history",
    description=(
        "[4.1] #13. Every credit that has moved in or out of this account, for "
        "an administrator investigating an anomaly.\n\n" + _ENTRIES_DESCRIPTION
    ),
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
    },
)
async def user_entries(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    session: DbSession,
    cursor: PageCursor = None,
    limit: PageLimit = None,
) -> LedgerEntryListResponse:
    return await _entries(session, user_id, cursor=cursor, limit=limit)


@router.get(
    "/markets/{market_id}/preview",
    response_model=PreviewOut,
    summary="What a trade would cost, before confirming it",
    description=_PREVIEW_DESCRIPTION,
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        404: {
            "description": (
                "No such market — including a draft or a submitted one, "
                "which market_service refuses with the same 404."
            )
        },
        409: {
            "description": (
                "This sell is larger than the outcome's shares outstanding."
            )
        },
        422: {
            "description": (
                "A malformed query string, an `outcome_id` that is not this "
                "market's, a quantity whose cost or resulting shares "
                "outstanding exceed what the ledger can store, or a trade "
                "whose total rounds to nothing (D-041): `proceeds_below_tick` "
                "on a sell, `cost_below_tick` on a buy."
            )
        },
        500: {
            "description": (
                "`market_book_incomplete`: this service holds a book for the "
                "market that cannot be priced — no outcome rows, one of "
                "them, or a `liquidity_b` the engine cannot use. Only a "
                "hand-run repair or a half-applied migration produces it. A "
                "server fault; not worth retrying."
            )
        },
        503: {"description": "market_service could not be reached right now."},
    },
)
async def preview_trade(
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
    side: Side,
    quantity: PreviewQuantity,
    access_token: AccessToken,
    session: DbSession,
) -> PreviewOut:
    result = await preview_service.quote(
        session,
        market_id,
        outcome_id=outcome_id,
        side=side,
        quantity=quantity,
        access_token=access_token,
    )
    return PreviewOut(
        market_id=result.market_id,
        state_version=result.state_version,
        side=result.side,
        outcome_id=result.outcome_id,
        quantity=result.quantity,
        total=result.total,
        average_price=result.average_price,
        prices=[
            OutcomePriceOut(outcome_id=p.outcome_id, position=p.position, price=p.price)
            for p in result.prices
        ],
        post_trade_prices=[
            OutcomePriceOut(outcome_id=p.outcome_id, position=p.position, price=p.price)
            for p in result.post_trade_prices
        ],
    )


_SNAPSHOT_DESCRIPTION = (
    "[F-9] #112. The authoritative price read: what a client renders when it "
    "opens a page, and what it re-fetches on every reconnect "
    "(`docs/api/realtime-service.md`).\n\n"
    "Any valid access token, any role (D-018), like the preview beside it.\n\n"
    "The body is the `price` socket frame without its `type` — "
    "`market_id`, `state_version`, `prices`, `occurred_at` — byte-for-byte, "
    "so a client renders a snapshot and a price frame with one function.\n\n"
    "**The first request on a market writes.** A market nobody has touched "
    "yet has no book: this route opens and funds one, once per market ever, "
    "which can take up to the market-terms timeout. Every request after "
    "that is a single indexed read.\n\n"
    "**Never checks whether the market is still open (ADR 0017).** A closed "
    "market still has a price to render — the last one anybody traded at — "
    "and the reconnect sequence needs this route to return a number rather "
    "than an error."
)


@router.get(
    "/markets/{market_id}/snapshot",
    response_model=SnapshotOut,
    summary="The market's authoritative current price",
    description=_SNAPSHOT_DESCRIPTION,
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        404: {"description": "No such market."},
        422: {"description": "`market_id` is not a UUID."},
        500: {
            "description": (
                "`market_book_incomplete`: this service holds a book for the "
                "market with no outcome rows. A server fault, not retryable."
            )
        },
        503: {"description": "market_service could not be reached right now."},
    },
)
async def market_snapshot(
    market_id: uuid.UUID,
    access_token: AccessToken,
    session: DbSession,
) -> SnapshotOut:
    result = await snapshot_service.snapshot(
        session, market_id, access_token=access_token
    )
    return SnapshotOut(
        market_id=result.market_id,
        state_version=result.state_version,
        prices=[
            OutcomePriceOut(outcome_id=p.outcome_id, position=p.position, price=p.price)
            for p in result.prices
        ],
        occurred_at=result.occurred_at,
    )


_TRADE_DESCRIPTION = (
    "[T-2] #22. Buy shares in an open market. This service's first write "
    "route: it takes no account, no amount and no leg — the request model "
    "is `extra=\"forbid\"`, and the debited account is the caller's own, "
    "read from the token's `sub` (ADR 0009's amendment).\n\n"
    "**A retry answers before a status check runs.** The idempotency "
    "lookup is unlocked and first: a hit is compared against this request "
    "and replayed — no HTTP call to market_service, no lock — even on a "
    "market that has since closed, so a trade that committed and lost its "
    "response is never told `market_closed` for a trade that in fact "
    "charged the trader (ADR 0017).\n\n"
    "**The stored idempotency key is derived**, "
    "`trade:<user_id>:<market_id>:<idempotency_key>` — the client's string "
    "is never stored as sent, so it only has to be unique to the caller "
    "who sent it.\n\n"
    "**`state_version` is required and compared under the book's own lock, "
    "for strict equality.** Either direction — older or newer than the "
    "book — is `409 quote_stale`, with `quoted` and `current` in "
    "`error.details`.\n\n"
    "Buy only. A sell is refused `422` until [T-3] #23 adds the per-user "
    "holdings check this route needs before it can accept one.\n\n"
    "On success, a price event publishes after the transaction commits; a "
    "publish failure never fails the trade and never surfaces here."
)


@router.post(
    "/markets/{market_id}/trades",
    response_model=TradeOut,
    status_code=201,
    summary="Buy shares in an open market",
    description=_TRADE_DESCRIPTION,
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        404: {"description": "No such market."},
        409: {
            "description": (
                "The market is not open for trading, the quoted "
                "`state_version` is stale, this account cannot afford the "
                "trade, or this idempotency key already names a different "
                "trade."
            )
        },
        422: {
            "description": (
                "A malformed body, an extra field, `side` other than "
                "\"buy\", a quantity at five decimal places, <= 0 or wider "
                "than 18 digits, an `outcome_id` that is not this market's, "
                "a quantity whose cost or resulting shares outstanding "
                "exceed what the ledger can store, or a buy whose cost "
                "rounds to nothing (`cost_below_tick`, D-041)."
            )
        },
        500: {
            "description": (
                "`market_book_incomplete`: this service holds a book for the "
                "market that cannot be priced — no outcome rows, one of "
                "them, or a `liquidity_b` the engine cannot use. A server "
                "fault; not worth retrying."
            )
        },
        503: {"description": "market_service could not be reached right now."},
    },
)
async def buy_shares(
    market_id: uuid.UUID,
    body: TradeIn,
    user: CurrentUser,
    access_token: AccessToken,
    session: DbSession,
    redis: RedisClient,
) -> TradeOut:
    result = await trading.execute(
        session,
        market_id,
        user_id=user.user_id,
        outcome_id=body.outcome_id,
        side=Side.BUY,
        quantity=body.quantity,
        state_version=body.state_version,
        idempotency_key=body.idempotency_key,
        access_token=access_token,
        redis_client=redis,
    )
    return TradeOut(
        transaction_id=result.transaction_id,
        user_id=result.user_id,
        market_id=result.market_id,
        outcome_id=result.outcome_id,
        side=Side(result.side),
        quantity=result.quantity,
        total=result.total,
        state_version=result.state_version,
    )


async def _balance(session: AsyncSession, user_id: uuid.UUID) -> BalanceOut:
    """One answer, two routes.

    The `/me` and admin routes differ in who may call them and in nothing else,
    so the difference lives in their dependencies and the work lives here.
    """
    balance = await ledger_service.balance_of_user(session, user_id)
    return BalanceOut(
        user_id=user_id, account_id=balance.account_id, balance=balance.amount
    )


async def _entries(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    cursor: str | None,
    limit: int | None,
) -> LedgerEntryListResponse:
    page = await ledger_service.history_for_user(
        session, user_id, limit=_page_size(limit), cursor=cursor
    )
    return LedgerEntryListResponse(
        entries=[
            LedgerEntryOut.of(row.entry, balance_after=row.balance_after)
            for row in page.rows
        ],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )
