"""HTTP routes for the ledger service.

Thin by design: parse, delegate to the service layer, choose a status code. No
business rule lives here, and no route builds an error response by hand. Domain
errors raised below the controller are turned into JSON by
`errors.register_error_handlers`.

Nothing edits or removes a ledger entry, ever, and there is no PUT, PATCH or
DELETE anywhere on this service. That is the whole design, it is enforced by
a trigger rather than by this file being short, and a route added here in a
hurry would fail against the database.

**Five routes write, and none takes money.** `POST /markets/{market_id}/trades`
([T-2] #22) answers ADR 0009's deferred service-auth question for exactly this
shape of caller: the request model is `extra="forbid"` over five fields, none
of them money, so the trader's account is `accounts.ensure(USER, claims.sub)`
rather than anything in the body, and a trader's own token is safe to accept
because there is nothing in the request for it to mint. See ADR 0009's
amendment. `POST /markets/{market_id}/orders` ([T-6] #26) is the same shape:
`OrderIn` names a price and a quantity, and the budget is computed from them
(ADR 0024). `POST /orders/{order_id}/cancel` ([T-8] #28) takes an empty body,
and what it releases is derived from the caller's own order row.
`POST /admin/orders/{order_id}/cancel` ([T-8] #326) is the same cancel for an
administrator, of any trader's order, and its body is only the reason.

`POST /markets/{market_id}/settlement` ([3.4] #12) takes an administrator's
token and an empty body: the winner comes from market_service and the holders
and amounts from the ledger's own positions (ADR 0009's second amendment, ADR
0019). It publishes nothing, so unlike the trade it holds no Redis client.

The grant is the older exception that proves the same rule from the read
side: a user reading their own balance can write, because [B-1] #32's starting
credits are minted lazily on first read. See `service/grants.py`. An
administrator reading somebody else's writes nothing (#188).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError
from pydantic import AwareDatetime

from controller.dependencies import (
    AccessToken,
    AuthClient,
    CurrentActor,
    CurrentAdmin,
    CurrentUser,
    DbSession,
    RedisClient,
    TermsClient,
)
from core.config import get_settings
from core.pricing import Side
from model.schemas import (
    AdminCancelIn,
    BalanceOut,
    CancelIn,
    CancellationOut,
    ExposureOut,
    ExposureOutcomeOut,
    FillOut,
    LeaderboardPageOut,
    LedgerEntryListResponse,
    LedgerEntryOut,
    MarketTradeListResponse,
    OrderIn,
    OrderListOut,
    OrderOut,
    OutcomePriceOut,
    PortfolioOut,
    PortfolioPositionOut,
    PlacementOut,
    PreviewOut,
    PriceHistoryOut,
    PricePointOut,
    SettlementIn,
    SettlementOut,
    SnapshotOut,
    TradeIn,
    TradeOut,
    ValidationErrorOut,
)
from service import (
    cancellation,
    exposure as exposure_service,
    leaderboard as leaderboard_service,
    ledger_service,
    orders,
    portfolio as portfolio_service,
    preview as preview_service,
    price_history as price_history_service,
    settlement,
    snapshot as snapshot_service,
    trading,
)
from service.book_prices import PricedOutcome

router = APIRouter(prefix="/ledger", tags=["ledger"])

_BALANCE_DESCRIPTION = (
    "[B-2] #33. The account's credits, derived by summing every entry on the "
    "account. There is no stored balance for this number to disagree with.\n\n"
    "`reserved` is what the user's live buy orders could still spend, and "
    "`available` is `balance - reserved`, what a trade can spend (ADR 0024). "
    "Both are read in the same statement as the balance.\n\n"
    "Exact decimal strings, not JSON numbers. A balance that has been through "
    "an IEEE double is a balance that can be wrong by an epsilon, and this is "
    "money."
)

_FIRST_READ_MINTS = (
    "\n\nThe first call for a user also mints their starting credits "
    "([B-1] #32), as a real append-only transaction, keyed on the user id so it "
    "can happen exactly once. Every later call just reads."
)

_ADMIN_READ_MINTS_NOTHING = (
    "\n\n**Mints nothing.** This service cannot tell a user's id from any other "
    "uuid, and a grant is permanent, so an id the ledger has no account for "
    "reads as a zero balance with a null `account_id`, and an empty history. "
    "A new user's starting credits appear here after their own first read."
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


def _invalid_request(description: str) -> dict[str, object]:
    """A route's 422 entry: its own description, the one envelope. #117

    Without the model, `/docs` describes FastAPI's `HTTPValidationError`,
    which this service never sends: `errors.py` answers every 422 as
    `ValidationErrorOut`.
    """
    return {"model": ValidationErrorOut, "description": description}


def _page_size(limit: int | None) -> int:
    """Clamped rather than rejected.

    A caller asking for more than the ceiling wants as much as it can get, and
    a 422 on `limit=1000` is a worse answer than the 200 rows the server is
    willing to serve. Same rule as the audit feed.
    """
    settings = get_settings()
    return min(limit or settings.default_page_size, settings.max_page_size)


def _prices_out(priced: list[PricedOutcome]) -> list[OutcomePriceOut]:
    """The wire form of a market's priced outcomes, in the order given."""
    return [
        OutcomePriceOut(outcome_id=p.outcome_id, position=p.position, price=p.price)
        for p in priced
    ]


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
    summary="My balance, what my orders reserve, and what is available",
    description=_BALANCE_DESCRIPTION + _FIRST_READ_MINTS,
    responses={401: {"description": "Missing, malformed or expired access token."}},
)
async def my_balance(user: CurrentUser, session: DbSession) -> BalanceOut:
    balance = await ledger_service.balance_of_user(session, user.user_id)
    return _balance_out(user.user_id, balance)


@router.get(
    "/entries/me",
    response_model=LedgerEntryListResponse,
    summary="My ledger history",
    description=_ENTRIES_DESCRIPTION + _FIRST_READ_MINTS,
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        401: {"description": "Missing, malformed or expired access token."},
        422: _invalid_request("`invalid_request`: `limit` below 1."),
    },
)
async def my_entries(
    user: CurrentUser,
    session: DbSession,
    cursor: PageCursor = None,
    limit: PageLimit = None,
) -> LedgerEntryListResponse:
    page = await ledger_service.history_for_user(
        session, user.user_id, limit=_page_size(limit), cursor=cursor
    )
    return _entries_out(page)


_PORTFOLIO_DESCRIPTION = (
    "[T-4] #24. The caller's own positions and their current value, each "
    "valued at liquidation (ADR 0018) rather than at the marginal price: "
    "`value` is what selling the whole position now would credit, never "
    "`quantity * price`.\n\n"
    "The balance and every position are read together, in one statement, "
    "with no lock, after minting the caller's starting credits on their "
    "first read exactly as `/balances/me` does. Makes no call to "
    "market_service — a market that has closed is valued at its frozen "
    "book like any other.\n\n"
    "A market this service has settled is not valued: its rows carry "
    "`result` and `payout`, and `price`, `value` and `unrealized_pnl` are "
    "null. The payout is already in `balance`, so such a row adds nothing "
    "to `positions_value`. Every key is on every row; branch on `result`.\n\n"
    "[B-1] #359. `starting_credits` is what this user's grant actually "
    "paid, read from that transaction's entry rather than from the "
    "`STARTING_CREDITS` setting, which can change without rewriting an "
    "entry (ADR 0009). `realized_pnl` is what the sells and the settlements "
    "banked, `unrealized_pnl` is the sum of the rows' own, and `total_pnl` "
    "is both — which is `net_worth - starting_credits`, exactly. Nothing is "
    "stored: every figure is derived from the entries on each read.\n\n"
    "Only positions with quantity > 0 are shown, ordered by `market_id` "
    "then `outcome_position`; there is no admin variant."
)


@router.get(
    "/portfolio/me",
    response_model=PortfolioOut,
    summary="My positions and their current value",
    description=_PORTFOLIO_DESCRIPTION,
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        500: {
            "description": (
                "`market_book_incomplete`: a held market's book cannot be "
                "priced, or a position exceeds its outcome's shares "
                "outstanding. The whole read fails; no partial portfolio is "
                "returned."
            )
        },
    },
)
async def my_portfolio(user: CurrentUser, session: DbSession) -> PortfolioOut:
    result = await portfolio_service.portfolio_of_user(session, user.user_id)
    return PortfolioOut(
        user_id=result.user_id,
        account_id=result.account_id,
        balance=result.balance,
        positions_value=result.positions_value,
        net_worth=result.net_worth,
        starting_credits=result.starting_credits,
        realized_pnl=result.realized_pnl,
        unrealized_pnl=result.unrealized_pnl,
        total_pnl=result.total_pnl,
        positions=[
            PortfolioPositionOut(
                market_id=p.market_id,
                outcome_id=p.outcome_id,
                outcome_position=p.outcome_position,
                quantity=p.quantity,
                cost_basis=p.cost_basis,
                average_entry_price=p.average_entry_price,
                price=p.price,
                value=p.value,
                unrealized_pnl=p.unrealized_pnl,
                result=p.result,
                payout=p.payout,
                state_version=p.state_version,
            )
            for p in result.positions
        ],
    )


@router.get(
    "/users/{user_id}/balance",
    response_model=BalanceOut,
    summary="Any user's balance",
    description=(
        "[4.1] #13. The same number the user sees, for an administrator "
        "investigating an anomaly.\n\n"
        + _BALANCE_DESCRIPTION
        + _ADMIN_READ_MINTS_NOTHING
    ),
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
        422: _invalid_request("`invalid_request`: `user_id` is not a UUID."),
    },
)
async def user_balance(
    user_id: uuid.UUID, admin: CurrentAdmin, session: DbSession
) -> BalanceOut:
    balance = await ledger_service.balance_for_admin(session, user_id)
    return _balance_out(user_id, balance)


@router.get(
    "/users/{user_id}/entries",
    response_model=LedgerEntryListResponse,
    summary="Any user's ledger history",
    description=(
        "[4.1] #13. Every credit that has moved in or out of this account, for "
        "an administrator investigating an anomaly.\n\n"
        + _ENTRIES_DESCRIPTION
        + _ADMIN_READ_MINTS_NOTHING
    ),
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
        422: _invalid_request(
            "`invalid_request`: `user_id` is not a UUID, or `limit` below 1."
        ),
    },
)
async def user_entries(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    session: DbSession,
    cursor: PageCursor = None,
    limit: PageLimit = None,
) -> LedgerEntryListResponse:
    page = await ledger_service.history_for_admin(
        session, user_id, limit=_page_size(limit), cursor=cursor
    )
    return _entries_out(page)


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
        422: _invalid_request(
            "`invalid_request`: a malformed query string. "
            "`unknown_outcome`: an `outcome_id` that is not this market's. "
            "`quantity_too_large`: a quantity whose cost or resulting shares "
            "outstanding exceed what the ledger can store. A trade whose "
            "total rounds to nothing (D-041): `proceeds_below_tick` on a "
            "sell, `cost_below_tick` on a buy."
        ),
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
    terms_client: TermsClient,
    session: DbSession,
) -> PreviewOut:
    result = await preview_service.quote(
        session,
        market_id,
        outcome_id=outcome_id,
        side=side,
        quantity=quantity,
        access_token=access_token,
        terms_client=terms_client,
    )
    return PreviewOut(
        market_id=result.market_id,
        state_version=result.state_version,
        side=result.side,
        outcome_id=result.outcome_id,
        quantity=result.quantity,
        total=result.total,
        average_price=result.average_price,
        payout_if_wins=result.payout_if_wins,
        profit_if_wins=result.profit_if_wins,
        prices=_prices_out(result.prices),
        post_trade_prices=_prices_out(result.post_trade_prices),
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
        422: _invalid_request("`invalid_request`: `market_id` is not a UUID."),
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
    terms_client: TermsClient,
    session: DbSession,
) -> SnapshotOut:
    result = await snapshot_service.snapshot(
        session, market_id, access_token=access_token, terms_client=terms_client
    )
    return SnapshotOut(
        market_id=result.market_id,
        state_version=result.state_version,
        prices=_prices_out(result.prices),
        occurred_at=result.occurred_at,
    )


_EXPOSURE_DESCRIPTION = (
    "[2.2] #6. What resolving a market would cost: for each outcome, the "
    "shares outstanding, what the market would pay if that outcome won, and "
    "what the pool would have left over. Any administrator, not only the "
    "market's creator.\n\n"
    "`payout_exceeds_pool` is true when any payout is strictly greater than "
    "the pool, not the seed. One statement reads `q` and the pool together, "
    "with no lock. Once `result` is set, `pool`, `payout_if_wins` and "
    "`residue_if_wins` are null, and `payout_exceeds_pool` is false.\n\n"
    "**Writes nothing and asks market_service nothing.** A market with no "
    "book here, including an id that names no market, is a `200` with nulls "
    "and an empty `outcomes`. Never checks the market's status."
)


@router.get(
    "/markets/{market_id}/exposure",
    response_model=ExposureOut,
    summary="What resolving the market would pay out",
    description=_EXPOSURE_DESCRIPTION,
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
        422: _invalid_request("`invalid_request`: `market_id` is not a UUID."),
    },
)
async def market_exposure(
    market_id: uuid.UUID, admin: CurrentAdmin, session: DbSession
) -> ExposureOut:
    result = await exposure_service.exposure_of_market(session, market_id=market_id)
    return ExposureOut(
        market_id=result.market_id,
        opened_at=result.opened_at,
        state_version=result.state_version,
        seed_subsidy=result.seed_subsidy,
        pool=result.pool,
        payout_exceeds_pool=result.payout_exceeds_pool,
        result=result.result,
        winning_outcome_id=result.winning_outcome_id,
        outcomes=[
            ExposureOutcomeOut(
                outcome_id=outcome.outcome_id,
                position=outcome.position,
                shares_outstanding=outcome.shares_outstanding,
                payout_if_wins=outcome.payout_if_wins,
                residue_if_wins=outcome.residue_if_wins,
            )
            for outcome in result.outcomes
        ],
    )


_TRADE_DESCRIPTION = (
    "[T-2] #22, [T-3] #23. Buy or sell shares in an open market. This "
    "service's first write route: it takes no account, no amount and no "
    "leg — the request model is `extra=\"forbid\"`, and the trader's account is the caller's own, "
    "read from the token's `sub` (ADR 0009's amendment). The other leg is "
    "this market's pool: on a buy the trader is debited and the pool "
    "credited, on a sell the reverse.\n\n"
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
    "**A sell is checked against the caller's own position**, under the "
    "same lock, and refused `409 insufficient_shares_held` if it is larger. "
    "`total` is negative on a buy and positive on a sell.\n\n"
    "On success, a price event publishes after the transaction commits; a "
    "publish failure never fails the trade and never surfaces here."
)


@router.post(
    "/markets/{market_id}/trades",
    response_model=TradeOut,
    status_code=201,
    summary="Trade shares in an open market",
    description=_TRADE_DESCRIPTION,
    responses={
        401: {
            "description": (
                "`invalid_token`: missing, malformed or expired access token."
            )
        },
        403: {
            "description": (
                "`account_suspended`: an administrator has suspended this "
                "account. Nothing was written (ADR 0022)."
            )
        },
        404: {"description": "`market_not_found`: no such market."},
        409: {
            "description": (
                "`market_closed`: the market is not open for trading. "
                "`quote_stale`: the quoted `state_version` is stale. "
                "`insufficient_funds`: a buy this account's available "
                "credits, its balance less what its buy orders reserve, "
                "cannot cover. "
                "`insufficient_shares_held`: a sell larger than this "
                "caller's position in this outcome less what their sell "
                "orders in it reserve. "
                "`idempotency_key_reused`: this key already names a "
                "different trade."
            )
        },
        422: _invalid_request(
            "`invalid_request`: a malformed body, an extra field, `side` "
            "other than \"buy\" or \"sell\", a quantity at five decimal "
            "places, <= 0 or wider than 18 digits. `unknown_outcome`; "
            "`quantity_too_large`; `cost_below_tick` on a buy whose cost "
            "rounds to nothing; `proceeds_below_tick` on a sell whose "
            "proceeds round to nothing (D-041)."
        ),
        500: {
            "description": (
                "`market_book_incomplete`: this service holds a book for the "
                "market that cannot be priced — no outcome rows, one of "
                "them, or a `liquidity_b` the engine cannot use. "
                "`cascade_fill_refused`: the overdraft check refused a resting "
                "order this trade filled, which ADR 0024 makes impossible; "
                "nothing was written. Both are server faults; not worth "
                "retrying."
            )
        },
        503: {
            "description": (
                "`market_terms_unavailable`: market_service could not be "
                "reached right now, or returned unusable terms on a first "
                "trade. `auth_unavailable`: the auth service could not say "
                "whether this account may trade."
            )
        },
    },
)
async def execute_trade(
    market_id: uuid.UUID,
    body: TradeIn,
    user: CurrentUser,
    access_token: AccessToken,
    terms_client: TermsClient,
    auth_client: AuthClient,
    session: DbSession,
    redis: RedisClient,
) -> TradeOut:
    result = await trading.execute(
        session,
        market_id,
        user_id=user.user_id,
        outcome_id=body.outcome_id,
        side=body.side,
        quantity=body.quantity,
        state_version=body.state_version,
        idempotency_key=body.idempotency_key,
        access_token=access_token,
        terms_client=terms_client,
        auth_client=auth_client,
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
        book_state_version=result.book_state_version,
    )


_PLACE_ORDER_DESCRIPTION = (
    "[T-6] #26, ADR 0024. Place a limit order: buy or sell at `limit_price` or "
    "better, against the market maker. As much as the price allows fills at "
    "once, in `fills`, and the rest rests. A later trade that moves the "
    "price past the limit fills it inside that trade ([T-7] #27).\n\n"
    "**Takes no account and no amount**, and no `state_version`: the limit "
    "protects the trader. A buy's budget, `limit_price × quantity` rounded up "
    "to the tick, has to fit in `available` even when it would fill "
    "completely for less.\n\n"
    "A retry with the same `idempotency_key` is `201` with the order as it "
    "stands now and the fills the first request made. The stored key is "
    "`order:<user_id>:<market_id>:<idempotency_key>`."
)


def _order_out(order: orders.OrderView) -> OrderOut:
    return OrderOut(
        id=order.id,
        market_id=order.market_id,
        outcome_id=order.outcome_id,
        outcome_position=order.outcome_position,
        side=order.side,
        limit_price=order.limit_price,
        quantity=order.quantity,
        filled_quantity=order.filled_quantity,
        average_fill_price=order.average_fill_price,
        reserved_credits=order.reserved_credits,
        reserved_shares=order.reserved_shares,
        status=order.status,
        created_at=order.created_at,
        cancelled_at=order.cancelled_at,
    )


@router.post(
    "/markets/{market_id}/orders",
    response_model=PlacementOut,
    status_code=201,
    summary="Place a limit order in an open binary market",
    description=_PLACE_ORDER_DESCRIPTION,
    responses={
        401: {
            "description": (
                "`invalid_token`: missing, malformed or expired access token."
            )
        },
        403: {
            "description": (
                "`account_suspended`: an administrator has suspended this "
                "account. Nothing was written (ADR 0022)."
            )
        },
        404: {"description": "`market_not_found`: no such market."},
        409: {
            "description": (
                "`market_closed`: the market is not open, or has a result. "
                "`market_not_binary`: it has more than two outcomes. "
                "`order_crosses_book`: a resting order pushing the price the "
                "other way is in the way; `error.details` carries `side` and "
                "`max_limit_price` or `min_limit_price`. "
                "`insufficient_funds`: the budget does not fit in "
                "`available`; `error.details` carries `balance`, `reserved`, "
                "`available` and `shortfall`. "
                "`insufficient_shares_held`: a sell larger than the position "
                "less what the caller's other sell orders reserve. "
                "`idempotency_key_reused`: the key names a different order."
            )
        },
        422: _invalid_request(
            "`invalid_request`: a malformed body, an extra field, a `side` "
            "other than \"buy\" or \"sell\", a `quantity` <= 0, at five "
            "decimal places or wider than 18 digits, or a `limit_price` at or "
            "outside 0 and 1 or at five decimal places. `unknown_outcome`; "
            "`quantity_too_large` on a fill the ledger could not store."
        ),
        500: {
            "description": (
                "`market_book_incomplete`: this service holds a book for the "
                "market that cannot be priced. "
                "`cascade_fill_refused`: the overdraft check refused a resting "
                "order this placement's fill set off, which ADR 0024 makes "
                "impossible; nothing was written. Both are server faults."
            )
        },
        503: {
            "description": (
                "`market_terms_unavailable`: market_service could not be "
                "reached. `auth_unavailable`: the auth service could not say "
                "whether this account may trade. Nothing was written."
            )
        },
    },
)
async def place_order(
    market_id: uuid.UUID,
    body: OrderIn,
    user: CurrentUser,
    access_token: AccessToken,
    terms_client: TermsClient,
    auth_client: AuthClient,
    session: DbSession,
    redis: RedisClient,
) -> PlacementOut:
    placement = await orders.place(
        session,
        market_id,
        user_id=user.user_id,
        outcome_id=body.outcome_id,
        side=body.side,
        limit_price=body.limit_price,
        quantity=body.quantity,
        idempotency_key=body.idempotency_key,
        access_token=access_token,
        terms_client=terms_client,
        auth_client=auth_client,
        redis_client=redis,
    )
    return PlacementOut(
        order=_order_out(placement.order),
        fills=[
            FillOut(
                transaction_id=fill.transaction_id,
                quantity=fill.quantity,
                total=fill.total,
                state_version=fill.state_version,
            )
            for fill in placement.fills
        ],
        book_state_version=placement.book_state_version,
    )


@router.get(
    "/orders/me",
    response_model=OrderListOut,
    summary="My live limit orders",
    description=(
        "[T-6] #26. The caller's live orders, newest first: open, on a market "
        "with no result, closed markets included. Filled, cancelled and "
        "released orders are not listed. No paging. Any valid access token."
    ),
    responses={
        401: {
            "description": (
                "`invalid_token`: missing, malformed or expired access token."
            )
        },
    },
)
async def my_orders(user: CurrentUser, session: DbSession) -> OrderListOut:
    live = await orders.list_live(session, user.user_id)
    return OrderListOut(orders=[_order_out(order) for order in live])


_CANCEL_ORDER_DESCRIPTION = (
    "[T-8] #28, ADR 0024. Cancel one of the caller's limit orders and release "
    "what it still holds: a buy's budget less what it has spent, or a sell's "
    "unsold shares. Fills stay. An empty body, any role, the caller's own "
    "orders only.\n\n"
    "**No account check and no market gate**: a suspended account can still "
    "cancel while its token is valid, and a closed market's orders can be "
    "cancelled.\n\n"
    "**A cancel and a fill never both win.** Both take the market's book lock; "
    "whichever takes it first commits first. Writes no entry: a reservation "
    "is derived from the order, so a retried cancel releases nothing twice."
)


def _cancellation_out(cancelled: cancellation.Cancellation) -> CancellationOut:
    return CancellationOut(
        order=_order_out(cancelled.order),
        released_credits=cancelled.released_credits,
        released_shares=cancelled.released_shares,
    )


@router.post(
    "/orders/{order_id}/cancel",
    response_model=CancellationOut,
    status_code=200,
    summary="Cancel one of my limit orders",
    description=_CANCEL_ORDER_DESCRIPTION,
    responses={
        401: {
            "description": (
                "`invalid_token`: missing, malformed or expired access token."
            )
        },
        404: {
            "description": (
                "`order_not_found`: no such order, or not the caller's. The two "
                "look the same on purpose."
            )
        },
        409: {
            "description": (
                "`order_already_filled`: it filled completely before the cancel. "
                "`order_already_cancelled`: it was already cancelled; a retried "
                "cancel lands here, so treat it as done. "
                "`order_released`: its market has a result, and the remainder is "
                "already released."
            )
        },
        422: _invalid_request(
            "`invalid_request`: an `order_id` that is not a UUID, or a body "
            "with any field."
        ),
    },
)
async def cancel_order(
    order_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    body: CancelIn | None = None,
) -> CancellationOut:
    # `body` is never read: it is declared so that a field in it is refused.
    cancelled = await cancellation.cancel(session, order_id, user_id=user.user_id)
    return _cancellation_out(cancelled)


_ADMIN_CANCEL_DESCRIPTION = (
    "[T-8] #326, ADR 0024. Cancel any trader's live limit order, as its owner's "
    "cancel would, and release its remainder. For an order whose owner cannot "
    "cancel it, such as a suspended account's once its token has expired.\n\n"
    "**A reason is required**, at least 10 characters once trimmed and at most "
    "500. It is recorded in the audit log as `order.cancelled_by_admin`, in the "
    "cancel's own transaction, and kept nowhere else.\n\n"
    "Exactly the orders the owner could cancel, with the owner's refusals. The "
    "same book lock, so it never races a fill or the owner's own cancel. Posts "
    "no ledger entry, and publishes no frame."
)


@router.post(
    "/admin/orders/{order_id}/cancel",
    response_model=CancellationOut,
    status_code=200,
    summary="Cancel any trader's limit order",
    description=_ADMIN_CANCEL_DESCRIPTION,
    responses={
        401: {
            "description": (
                "`invalid_token`: missing, malformed or expired access token."
            )
        },
        403: {"description": "`not_an_administrator`: authenticated, but a trader."},
        404: {"description": "`order_not_found`: no such order."},
        409: {
            "description": (
                "`order_already_filled`, `order_already_cancelled` or "
                "`order_released`: as on the owner's cancel."
            )
        },
        422: _invalid_request(
            "`invalid_request`: an `order_id` that is not a UUID, no `reason`, "
            "a `reason` over 500 characters, or any other field. "
            "`cancellation_incomplete`: a `reason` under 10 characters once "
            "trimmed."
        ),
    },
)
async def cancel_any_order(
    order_id: uuid.UUID,
    actor: CurrentActor,
    session: DbSession,
    body: AdminCancelIn,
) -> CancellationOut:
    by_admin = cancellation.AdminCancel(actor=actor, reason=body.reason)
    cancelled = await cancellation.cancel(session, order_id, user_id=None, admin=by_admin)
    return _cancellation_out(cancelled)


_TRADE_FEED_DESCRIPTION = (
    "[BE][2.4] #63. Every trade in one market, newest first, for an "
    "administrator investigating it. Each row names its trader by `user_id`; "
    "resolve names through auth's batch lookup.\n\n"
    "The market's pool account also holds the seed, the settlement payouts and "
    "the residue, and none of them appears here. There is no `balance_after`.\n\n"
    "**Writes nothing.** A market with no book, or an id that names no market, "
    "is an empty `200`; this never opens a book, grants credits or calls "
    "market_service, and does not check the market's status.\n\n"
    "Paged by keyset with the same cursor as `/ledger/entries/me`: a trade "
    "committed while you page is newer than every row on screen."
)


@router.get(
    "/markets/{market_id}/trades",
    response_model=MarketTradeListResponse,
    summary="A market's trades",
    description=_TRADE_FEED_DESCRIPTION,
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        401: {"description": "Missing, malformed or expired access token."},
        403: {"description": "Authenticated, but not an administrator."},
        422: _invalid_request(
            "`invalid_request`: `market_id` is not a UUID, or `limit` below 1."
        ),
    },
)
async def market_trades(
    market_id: uuid.UUID,
    admin: CurrentAdmin,
    session: DbSession,
    cursor: PageCursor = None,
    limit: PageLimit = None,
) -> MarketTradeListResponse:
    page = await ledger_service.list_market_trades(
        session, market_id, limit=_page_size(limit), cursor=cursor
    )
    return MarketTradeListResponse(
        trades=page.rows, next_cursor=page.next_cursor, has_more=page.has_more
    )


_LEADERBOARD_DESCRIPTION = (
    "[L-1] #38, [BE][L-2] #269. Every ranked user's rank, username and net "
    "worth, from the most recent ranking the ledger has computed, in rank "
    "order. Any valid access token.\n\n"
    "`me` is the caller's own row in the ranking this page came from, on this "
    "page or not, and `null` when the caller is not in it. `is_me` is true on "
    "the caller's own row. The caller is matched by the access token, and no "
    "user id is sent.\n\n"
    "`stale` is true when `computed_at` is more than two refresh intervals "
    "old. Before any refresh, `computed_at` is null, `stale` is true and "
    "`rows` is empty.\n\n"
    "A cursor holds you to the ranking its first page came from. The ledger "
    "keeps the ranking before the current one, so a cursor survives one "
    "refresh; after the second it is `409 leaderboard_refreshed`: start again "
    "from page one.\n\n"
    "**Writes nothing.** Unlike the balance, history and portfolio, reading "
    "the leaderboard does not mint the caller's starting grant, so a caller "
    "with no account gets `me: null`."
)


@router.get(
    "/leaderboard",
    response_model=LeaderboardPageOut,
    summary="The leaderboard",
    description=_LEADERBOARD_DESCRIPTION,
    responses={
        400: {"description": "`cursor` was not one this service issued."},
        401: {"description": "Missing, malformed or expired access token."},
        409: {
            "description": (
                "`leaderboard_refreshed`: the cursor's ranking has been "
                "replaced. Start again from page one."
            )
        },
        422: _invalid_request("`invalid_request`: `limit` below 1."),
    },
)
async def leaderboard(
    user: CurrentUser,
    session: DbSession,
    cursor: PageCursor = None,
    limit: PageLimit = None,
) -> LeaderboardPageOut:
    # DECISIONS.md, "One clock per request, read at the controller, Python's
    # not the transaction's": `stale` is judged against this one reading.
    now = datetime.now(UTC)
    page = await leaderboard_service.list_page(
        session,
        caller_id=user.user_id,
        limit=_page_size(limit),
        cursor=cursor,
        now=now,
    )
    return LeaderboardPageOut(
        computed_at=page.computed_at,
        stale=page.stale,
        ranked_users=page.ranked_users,
        me=page.me,
        rows=page.rows,
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


def _point_out(point: price_history_service.PricedPoint) -> PricePointOut:
    """The wire form of one price-history point."""
    return PricePointOut(
        state_version=point.state_version,
        occurred_at=point.occurred_at,
        prices=_prices_out(point.prices),
    )


PriceHistoryStart = Annotated[
    AwareDatetime | None,
    Query(
        alias="from",
        description=(
            "ISO-8601 time with an offset (`Z` or `+08:00`; send `+` as `%2B`). "
            "Points at or after it, plus the last point before it, at its own "
            "time, so a chart starts at the price the market had then."
        ),
    ),
]

PriceHistoryEnd = Annotated[
    AwareDatetime | None,
    Query(
        alias="to",
        description="ISO-8601 time with an offset. Points at or before it.",
    ),
]

_PRICE_HISTORY_DESCRIPTION = (
    "[BE][2.4] #63. Every price the market has had, one point per trade, "
    "oldest first, rebuilt from the market's trades on every read. Any valid "
    "access token, any role: every point has already been broadcast to every "
    "subscriber of the market's price frames.\n\n"
    "A point is a `price` socket frame without `type` and `market_id`. The "
    "first is the book's opening, `state_version` 0. Dedupe on "
    "`state_version`, never on time.\n\n"
    "**Nothing is bucketed or paged.** `from` and `to` bound the payload, and "
    "`from` also returns the last point before it, at its own `occurred_at`. "
    "`from` later than `to` is `422 invalid_request` naming both.\n\n"
    "**Settlement adds no point.** The series ends at the last trade, and "
    "`result`, `winning_outcome_id` and `result_recorded_at` report the "
    "outcome beside it.\n\n"
    "**Writes nothing and asks no other service anything.** A market with no "
    "book here, including an id that names no market, is a `200` with an "
    "empty `points`. It never checks the market's status."
)


def _refuse_if_window_reversed(
    start: AwareDatetime | None, end: AwareDatetime | None
) -> None:
    """Raise `RequestValidationError` for a `from` later than `to`, so the one
    handler in `controller/errors.py` answers it as `invalid_request`, with a
    `details` item for each of the two parameters. #117."""
    if start is None or end is None or start <= end:
        return

    raise RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("query", parameter),
                "msg": "`from` must not be later than `to`.",
                "input": value,
            }
            for parameter, value in (("from", start), ("to", end))
        ]
    )


@router.get(
    "/markets/{market_id}/price-history",
    response_model=PriceHistoryOut,
    summary="Every price the market has had",
    description=_PRICE_HISTORY_DESCRIPTION,
    responses={
        401: {"description": "Missing, malformed or expired access token."},
        422: _invalid_request(
            "`invalid_request`: `market_id` is not a UUID, `from` or `to` is "
            "not a time with an offset, or `from` is later than `to`."
        ),
        500: {
            "description": (
                "`market_book_incomplete`: the book cannot be priced. "
                "`price_history_inconsistent`: the market's trades skip or "
                "reorder a `state_version`. Server faults, not retryable."
            )
        },
    },
)
async def market_price_history(
    market_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    start: PriceHistoryStart = None,
    end: PriceHistoryEnd = None,
) -> PriceHistoryOut:
    _refuse_if_window_reversed(start, end)
    history = await price_history_service.price_history_of_market(
        session, market_id, start=start, end=end
    )
    return PriceHistoryOut(
        market_id=history.market_id,
        points=[_point_out(point) for point in history.points],
        result=history.result,
        winning_outcome_id=history.winning_outcome_id,
        result_recorded_at=history.result_recorded_at,
    )


_SETTLEMENT_DESCRIPTION = (
    "[3.4] #12. Pay every winning share of an approved market once, then "
    "tell market_service the market is settled. Administrators only; any "
    "administrator may settle, including the proposer and the approver "
    "(ADR 0019).\n\n"
    "**The request chooses nothing.** The body is empty: no body, `{}` or "
    "`null`. Any field is `422 invalid_request`, not ignored, because a "
    "dropped `outcome_id` would let a client believe it had picked the "
    "winner. The winner is the outcome market_service approved, and the "
    "holders and amounts come from this service's own positions, read under "
    "the book lock (ADR 0009's second amendment).\n\n"
    "**`200` on the request that pays and on every repeat**, with the same "
    "six fields rebuilt from the entries the first request wrote. A repeat "
    "writes nothing here and retries the last step, so it is how a "
    "`503 settlement_unconfirmed` is finished.\n\n"
    "**No price frame is published**: settlement moves neither `q` nor any "
    "position."
)


@router.post(
    "/markets/{market_id}/settlement",
    response_model=SettlementOut,
    status_code=200,
    summary="Pay out an approved market",
    description=_SETTLEMENT_DESCRIPTION,
    responses={
        401: {
            "description": (
                "`invalid_token`: missing, malformed or expired access "
                "token, or market_service refused the forwarded one."
            )
        },
        403: {"description": "`not_an_administrator`: authenticated, but a trader."},
        404: {"description": "`market_not_found`: no such market."},
        409: {
            "description": (
                "`market_not_approved`: nothing is recorded here and the "
                "market is in a status other than `approved` or `settled`. "
                "`dispute_window_open`: approved, but not yet `settleable`."
            )
        },
        422: _invalid_request(
            "`invalid_request`: a body with any field, a body that is not "
            "an object, or a `market_id` that is not a UUID."
        ),
        503: {
            "description": (
                "`market_terms_unavailable`: market_service could not be "
                "reached, or answered with terms no settlement can use; "
                "nothing was paid. `settlement_unconfirmed`: every winner is "
                "paid and the settlement is recorded, and market_service did "
                "not confirm. Repeat the request."
            )
        },
    },
)
async def settle_market(
    market_id: uuid.UUID,
    actor: CurrentActor,
    access_token: AccessToken,
    terms_client: TermsClient,
    session: DbSession,
    body: SettlementIn | None = None,
) -> SettlementOut:
    # `body` is never read: it is declared so that a field in it is refused.
    result = await settlement.pay_out(
        session,
        market_id,
        actor=actor,
        access_token=access_token,
        terms_client=terms_client,
    )
    return SettlementOut(
        market_id=result.market_id,
        outcome_id=result.outcome_id,
        recorded_at=result.recorded_at,
        holders_paid=result.holders_paid,
        total_paid=result.total_paid,
        residue=result.residue,
    )


def _balance_out(user_id: uuid.UUID, balance: ledger_service.UserBalance) -> BalanceOut:
    """One body, two routes: `/me` mints the caller's grant, the admin route
    mints nothing (#188), and the wire shape is the same."""
    return BalanceOut(
        user_id=user_id,
        account_id=balance.account_id,
        balance=balance.amount,
        reserved=balance.reserved,
        available=balance.available,
    )


def _entries_out(page: ledger_service.EntryPage) -> LedgerEntryListResponse:
    return LedgerEntryListResponse(
        entries=[
            LedgerEntryOut.of(row.entry, balance_after=row.balance_after, trade=row.trade)
            for row in page.rows
        ],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )
