"""Request and response contracts.

These generate the OpenAPI schema at /docs, which is the contract the admin
console codes against.

Everything here is read-only. There is no request model that writes an audit
entry, because this service has no write route: an entry is appended by the
service performing the action, in the same transaction as the action itself.
See docs/adr/0006-audit-log-write-path.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdminActionOut(BaseModel):
    """One entry, exactly as it was written.

    Every field is a snapshot taken at the moment of the action. None of it is
    resolved at read time, and none of it can have changed since: the row
    cannot be updated, and the names in it are copies rather than references.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime

    actor_id: uuid.UUID
    actor_username: str = Field(
        description="The actor's username as it stood when they acted.",
        examples=["ernest_t"],
    )
    actor_role: str = Field(
        description="The role they held when they acted, not their role now.",
        examples=["admin"],
    )

    action_type: str = Field(
        description=(
            "Dotted and namespaced by the entity acted on, for example "
            "`market.submitted`. New values appear as stories land; treat an "
            "unrecognised one as opaque rather than as an error."
        ),
        examples=["market.submitted"],
    )

    target_type: str = Field(examples=["market"])
    target_id: uuid.UUID | None
    target_label: str | None = Field(
        default=None,
        description=(
            "A human-readable name for the target, snapshotted at write time. "
            "The market's question, the suspended account's username."
        ),
    )

    reason: str | None = Field(
        default=None,
        description="Supplied by the admin, for the action types that require one.",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Action-specific detail, shaped by `action_type`. Render what you "
            "recognise and ignore the rest."
        ),
    )

    source_service: str = Field(
        description="Which service appended this entry.", examples=["market_service"]
    )

    @field_validator("occurred_at")
    @classmethod
    def _always_utc(cls, v: datetime) -> datetime:
        """Guarantee an explicit offset on the way out.

        Same guard as the other two services. A driver handing back a naive
        datetime would make one entry serialise with a trailing Z and another
        without, leaving the frontend to special-case which.
        """
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class AdminActionListResponse(BaseModel):
    """One page of the log, newest first."""

    actions: list[AdminActionOut]

    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass back as `cursor` for the next page. Null means this page is "
            "the end of the log. Opaque: echo it unmodified rather than "
            "constructing one."
        ),
    )

    has_more: bool = Field(
        description=(
            "Whether another page exists. Equivalent to `next_cursor` being "
            "non-null, and stated so a client can drive a 'load more' control "
            "without reasoning about the cursor at all."
        ),
    )
