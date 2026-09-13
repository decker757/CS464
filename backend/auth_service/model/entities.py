"""Persistence models for the auth service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base
from core.roles import UserRole


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Stored as the user typed it; uniqueness is enforced case-insensitively by
    # the functional indexes below, so "Ernest" cannot coexist with "ernest".
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # [4.2] #60 admin suspend/unsuspend reads and writes this flag.
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # [1.1] #1 needs the market service to recognise an administrator, and it
    # cannot read this table across the schema boundary, so the value is copied
    # into the access token. Everyone starts a trader; promotion is a manual
    # UPDATE until [4.4] #16 builds the real role management.
    #
    # native_enum=False stores a VARCHAR with a CHECK constraint rather than a
    # Postgres ENUM type. Adding a member to a native enum is a migration that
    # cannot run inside a transaction on older servers; widening a CHECK is an
    # ordinary one, and [4.4] #16 will add three members.
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            native_enum=False,
            length=16,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=UserRole.TRADER,
        server_default=UserRole.TRADER.value,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("uq_users_username_lower", func.lower(username), unique=True),
        Index("uq_users_email_lower", func.lower(email), unique=True),
    )


class RefreshToken(Base):
    """A revocable handle on a session.

    The raw token exists only in the client's cookie. Only its SHA-256 lands
    here, so reading this table does not let anyone resume a session.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or _utcnow()
        expires_at = self.expires_at
        # SQLite hands back naive datetimes; normalise before comparing.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return self.revoked_at is None and expires_at > now
