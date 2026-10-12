"""Fixtures the [L-1] #38 read tests share.

Three files share these: the pure rules (`core/test_leaderboard_read_rules.py`),
the read (`service/test_leaderboard_read.py`) and the route
(`controller/test_leaderboard_routes.py`). The spec is
`docs/api/ledger-service.md`'s `GET /ledger/leaderboard` and #270's entries in
DECISIONS.md.

**Generations are stored directly, not through the refresh.** The read's
contract is "serve what is committed", and only a direct insert can choose
unfriendly Decimals, the order of ids and inserts, or 205 rows. `store_generation`
writes only states a refresh can write: ranks 1..N, `ranked_users` N, one
committed transaction. Pruning is the refresh's behaviour, so the test that
needs it runs the real refresh (`leaderboard_fixtures.refresh_with`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from service import leaderboard as leaderboard_service
from shared.paging import encode_fields
from unit_test.trade_fixtures import entities, session_factory

PATH = "/ledger/leaderboard"

# One above `rank`'s Integer column maximum (2**31 - 1).
RANK_OVERFLOW = 2_147_483_648


async def read_page(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int = 50,
    cursor: str | None = None,
):
    """One page of the board through `list_page`, defaulting to the route's
    page size of 50."""
    return await leaderboard_service.list_page(
        session, limit=limit, cursor=cursor, now=now
    )


# --- cursors built by hand ------------------------------------------------
def rank_cursor(generation_id: uuid.UUID, last_rank: int | str) -> str:
    """A cursor in the format DECISIONS.md fixes: `encode_fields(generation_id,
    last_rank)`. Input only; no test compares the server's cursor against it
    except the one that pins the format."""
    return encode_fields(str(generation_id), str(last_rank))


# --- storing generations --------------------------------------------------
async def store_generation(
    *,
    computed_at: datetime,
    rows: Sequence[tuple[str, Decimal]],
    generation_id: uuid.UUID | None = None,
    user_ids: Sequence[uuid.UUID] | None = None,
) -> None:
    """Commit one generation whose entries are `rows` `(username, net_worth)`,
    ranked 1..N in the order given, in a session of its own. `user_ids`, if
    given, pairs with `rows`; otherwise every row gets a fresh one."""
    ents = entities()
    generation_id = generation_id or uuid.uuid4()
    owners = list(user_ids) if user_ids is not None else [uuid.uuid4() for _ in rows]
    async with session_factory()() as writer:
        await writer.execute(
            insert(ents.LeaderboardGeneration).values(
                id=generation_id, computed_at=computed_at, ranked_users=len(rows)
            )
        )
        if rows:
            await writer.execute(
                insert(ents.LeaderboardEntry),
                [
                    {
                        "generation_id": generation_id,
                        "rank": rank,
                        "user_id": owner,
                        "username": username,
                        "net_worth": net_worth,
                    }
                    for rank, ((username, net_worth), owner) in enumerate(
                        zip(rows, owners, strict=True), start=1
                    )
                ],
            )
        await writer.commit()


def numbered_rows(count: int) -> list[tuple[str, Decimal]]:
    """`count` rows whose net worth falls with their rank: `user_001` first."""
    return [
        (f"user_{number:03d}", Decimal(10_000 - number) + Decimal("0.1234"))
        for number in range(1, count + 1)
    ]


async def board_counts() -> tuple[int, int]:
    """Generations and entries, from a session of its own."""
    ents = entities()
    async with session_factory()() as reader:
        generations = (
            await reader.execute(
                select(func.count()).select_from(ents.LeaderboardGeneration)
            )
        ).scalar_one()
        entries = (
            await reader.execute(select(func.count()).select_from(ents.LeaderboardEntry))
        ).scalar_one()
    return generations, entries
