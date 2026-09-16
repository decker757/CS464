"""HTTP routes for the ledger service.

Thin by design: parse, delegate to the service layer, choose a status code. No
business rule lives here, and no route builds an error response by hand. Domain
errors raised below the controller are turned into JSON by
`errors.register_error_handlers`.

Every route reads. There is deliberately no POST, PUT, PATCH or DELETE:

- Nothing edits or removes a ledger entry, ever. That is the whole design, it
  is enforced by a trigger rather than by this file being short, and a route
  added here in a hurry would fail against the database.
- Nothing *writes* one over HTTP yet either. `service/posting.py` holds the
  write path, and the endpoint that exposes it belongs to [T-2] #22, together
  with the decision about how a trading service proves it is a trading service.
  A write route that trusted a trader's own token would be a route for minting
  yourself credits.

The grant is the exception that proves it: reading a balance can write, because
[B-1] #32's starting credits are minted lazily on first read. See
`service/grants.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

# DbSession is the injected annotation and belongs on route signatures only.
# The two private helpers below take a plain AsyncSession: FastAPI never looks
# at them, so an Annotated[..., Depends(...)] there would advertise an
# injection that does not happen.
from controller.dependencies import CurrentAdmin, CurrentUser, DbSession
from core.config import get_settings
from model.schemas import BalanceOut, LedgerEntryListResponse, LedgerEntryOut
from service import ledger_service

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
