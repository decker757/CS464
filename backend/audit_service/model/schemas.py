"""Response contracts, published at /docs for the admin console. [4.3] #15

Read-only: there is no write route, so no request model. ADR 0006.
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
        """Give a naive datetime UTC, so every entry serialises with an offset."""
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
