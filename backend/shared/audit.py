"""Appending to the shared audit log, as every writing service does. [4.3] #15

One copy because every writer must behave identically, and a drifted copy would
fail silently. ADR 0006 and its #135 amendment. Three details are load-bearing:

- `record` inserts on the caller's session and never commits, so the entry and
  the admin action it describes commit together or not at all. That is the
  whole durability story: no queue, no retry, no outbox.
- The id is generated in Python. A server default would need RETURNING, which
  needs SELECT, which no writer holds.
- `admin_actions` sits on its own `MetaData` and must never join any service's
  `Base.metadata`: `create_all` and the test rebuild would issue DDL against a
  table no service role may create or drop, and the service would die at boot.

What differs by design stays in each service: its `AdminAction` vocabulary in
`model/audit.py`, and the `source_service` it reports in `core/audit.py`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    insert,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

AUDIT_SCHEMA = "audit"

# Separate from every Base.metadata on purpose; see the module docstring.
audit_metadata = MetaData(schema=AUDIT_SCHEMA)

admin_actions = Table(
    "admin_actions",
    audit_metadata,
    Column("id", Uuid, primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("actor_username", String(32), nullable=False),
    Column("actor_role", String(16), nullable=False),
    Column("action_type", String(64), nullable=False),
    Column("target_type", String(32), nullable=False),
    Column("target_id", Uuid, nullable=True),
    Column("target_label", Text, nullable=True),
    Column("reason", Text, nullable=True),
    Column("context", JSONB, nullable=True),
    Column("source_service", String(32), nullable=False),
)


@dataclass(frozen=True)
class Actor:
    """Who is acting, as they were at that moment. Built in the controller.

    Written to the log as values, not a foreign key, so an entry survives a
    later rename, demotion or deletion. A snapshot also because only the auth
    service can resolve a user id (ADR 0003); nothing below the controller
    knows a JWT was involved.
    """

    id: uuid.UUID
    username: str
    role: str


@dataclass(frozen=True)
class Entry:
    """One line of the log, as the acting service describes it.

    Everything but the id and `source_service`, which `record` supplies.
    `occurred_at` defaults to now; tests inject it.
    """

    actor: Actor
    action_type: str
    target_type: str
    target_id: uuid.UUID | None = None
    target_label: str | None = None
    reason: str | None = None
    context: dict[str, Any] | None = None
    occurred_at: datetime | None = None


async def record(session: AsyncSession, entry: Entry, *, source_service: str) -> None:
    """Append `entry` on `session`, without committing it.

    Returns nothing: writers hold INSERT and not SELECT, so they cannot read
    back the row they wrote.
    """
    await session.execute(
        insert(admin_actions).values(
            id=uuid.uuid4(),
            occurred_at=entry.occurred_at or datetime.now(UTC),
            actor_id=entry.actor.id,
            actor_username=entry.actor.username,
            actor_role=entry.actor.role,
            action_type=entry.action_type,
            target_type=entry.target_type,
            target_id=entry.target_id,
            target_label=entry.target_label,
            reason=entry.reason,
            context=entry.context,
            source_service=source_service,
        )
    )
