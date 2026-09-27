"""Reading the audit log. [4.3] #15

SELECT only. There is no update, delete or redact function, and the grants and
trigger would refuse one anyway. ADR 0006.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.paging import decode_cursor, encode_cursor
from model.entities import AdminAction


@dataclass(frozen=True)
class ActionFilter:
    """What the caller narrowed the feed to. [4.3] #15's third criterion."""

    actor_id: uuid.UUID | None = None
    action_type: str | None = None


@dataclass(frozen=True)
class ActionPage:
    actions: list[AdminAction]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


async def list_actions(
    session: AsyncSession,
    *,
    filters: ActionFilter | None = None,
    limit: int,
    cursor: str | None = None,
) -> ActionPage:
    """Return one page of the log, newest first.

    Raises MalformedCursor. Fetches `limit + 1` rows so `has_more` is exact
    without a COUNT over a table that only grows.
    """
    filters = filters or ActionFilter()

    stmt = _ordered_query(filters)

    if cursor is not None:
        # Decoded before the query, so a bad value is a 400 rather than a
        # driver error and a 500.
        occurred_at, row_id = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(AdminAction.occurred_at, AdminAction.id) < tuple_(occurred_at, row_id)
        )

    rows = list((await session.execute(stmt.limit(limit + 1))).scalars())

    if len(rows) <= limit:
        return ActionPage(actions=rows, next_cursor=None)

    page = rows[:limit]
    last = page[-1]
    return ActionPage(actions=page, next_cursor=encode_cursor(last.occurred_at, last.id))


def _ordered_query(filters: ActionFilter) -> Select[tuple[AdminAction]]:
    """Build the filtered feed query, newest first with ties broken by id.

    The id tiebreak is half the keyset contract, and the order matches the
    indexes in sql/02-schemas.sql.
    """
    stmt = select(AdminAction)

    if filters.actor_id is not None:
        stmt = stmt.where(AdminAction.actor_id == filters.actor_id)

    if filters.action_type is not None:
        # Exact, not prefix: a prefix would fold a future
        # `market.submitted.reverted` into the `market.submitted` filter.
        stmt = stmt.where(AdminAction.action_type == filters.action_type)

    return stmt.order_by(AdminAction.occurred_at.desc(), AdminAction.id.desc())
