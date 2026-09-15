"""Reading the audit log. [4.3] #15

HTTP is not mentioned in this file, and neither is writing. The only statement
this service issues against `audit.admin_actions` is a SELECT; entries are
appended by the service performing the action, in the same transaction as the
action, and never through here. docs/adr/0006-audit-log-write-path.md is the
argument for why.

There is no `update`, `delete` or `redact` function below, and adding one would
not work if it were written: `audit_svc` holds no UPDATE or DELETE grant and a
trigger refuses both even for the table's owner. The absence here is the API
surface; the grants are the enforcement.
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
    """What the caller narrowed the feed to.

    Both fields are [4.3] #15's third acceptance criterion. Filtering by the
    target — every action taken against one market — is the obvious next one,
    and belongs in the story that needs it rather than here.
    """

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
    """One page of the log, newest first.

    `limit + 1` rows are fetched and the extra one is dropped. That is what
    makes `has_more` exact without a second COUNT query over a table which only
    grows, and a count is the wrong question anyway: the answer would be stale
    by the time it rendered.
    """
    filters = filters or ActionFilter()

    stmt = _ordered_query(filters)

    if cursor is not None:
        # Raises MalformedCursor, which the controller turns into a 400. Decode
        # before the query rather than letting a bad value reach the database,
        # where it would surface as a driver error and a 500.
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
    """Newest first, with the tie broken by id.

    The ordering is half of the keyset contract and matches every index in
    sql/02-schemas.sql. Ordering by `occurred_at` alone would leave rows
    written in the same transaction in an order Postgres is free to change
    between queries, which is exactly the case a cursor cannot survive.
    """
    stmt = select(AdminAction)

    if filters.actor_id is not None:
        stmt = stmt.where(AdminAction.actor_id == filters.actor_id)

    if filters.action_type is not None:
        # Matched exactly, not by prefix. `market.submitted` and
        # `market.submitted.reverted` are different actions, and a prefix match
        # would silently fold a future one into an existing filter.
        stmt = stmt.where(AdminAction.action_type == filters.action_type)

    return stmt.order_by(AdminAction.occurred_at.desc(), AdminAction.id.desc())
