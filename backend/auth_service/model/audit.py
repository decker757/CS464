"""The audit log, as a writer sees it. [4.3] #15, [4.4] #16

`auth_svc` holds INSERT on `audit.admin_actions` and nothing else. ADR 0006.
A copy of `market_service/model/audit.py` with its own `SOURCE_SERVICE` and
vocabulary; keep the two in step. Whether to share it is ADR 0006's call.

Never put this table on `Base.metadata`: `create_all` and the test truncate
would hit a table this role cannot create or truncate, and the service would
die at boot. ADR 0006.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

AUDIT_SCHEMA = "audit"

SOURCE_SERVICE = "auth_service"

# Separate from Base.metadata on purpose; see the module docstring.
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


class AdminAction(StrEnum):
    """The action types this service appends, namespaced by what was acted on.

    Here rather than a CHECK in SQL, which could abort the action it records.
    ADR 0006. Never add a login: the log is for decisions about other people,
    not a user's own traffic.
    """

    USER_ROLE_CHANGED = "user.role_changed"
