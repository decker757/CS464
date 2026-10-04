"""Baseline: the auth schema as create_all built it before #75. [F-5] #75

Revision ID: 0001
Revises:
Create Date: 2026-10-04

Written from the models and reviewed line by line; unit_test/test_migrations.py
proves it builds exactly what they build. `role` is a non-native Enum: a
varchar(16) with no CHECK, so the value list below is documentation, not a
constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_suspended", sa.Boolean(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("trader", "admin", name="user_role", native_enum=False, length=16),
            server_default="trader",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="auth",
    )
    op.create_index(
        "uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True, schema="auth"
    )
    op.create_index(
        "uq_users_username_lower",
        "users",
        [sa.text("lower(username)")],
        unique=True,
        schema="auth",
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="auth",
    )
    op.create_index(
        op.f("ix_auth_refresh_tokens_token_hash"),
        "refresh_tokens",
        ["token_hash"],
        unique=True,
        schema="auth",
    )
    op.create_index(
        op.f("ix_auth_refresh_tokens_user_id"),
        "refresh_tokens",
        ["user_id"],
        unique=False,
        schema="auth",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_refresh_tokens_user_id"), table_name="refresh_tokens", schema="auth")
    op.drop_index(op.f("ix_auth_refresh_tokens_token_hash"), table_name="refresh_tokens", schema="auth")
    op.drop_table("refresh_tokens", schema="auth")
    op.drop_index("uq_users_username_lower", table_name="users", schema="auth")
    op.drop_index("uq_users_email_lower", table_name="users", schema="auth")
    op.drop_table("users", schema="auth")
