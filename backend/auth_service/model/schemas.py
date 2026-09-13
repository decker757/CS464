"""Request and response contracts.

These generate the OpenAPI schema at /docs, which is the contract the frontend
codes against for [FE][A-1] #46, [FE][A-2] #47 and [FE][A-3] #48.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from core.config import get_settings

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, examples=["ernest_t"])
    email: EmailStr = Field(examples=["ernest@example.com"])
    password: str = Field(max_length=128, examples=["correct-horse-battery"])

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        v = v.strip()
        if not _USERNAME_RE.match(v):
            raise ValueError("Username may contain only letters, numbers, hyphens and underscores.")
        return v

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def _password_policy(cls, v: str) -> str:
        minimum = get_settings().password_min_length
        if len(v) < minimum:
            raise ValueError(f"Password must be at least {minimum} characters.")
        return v


class LoginRequest(BaseModel):
    """`identifier` accepts either the username or the email.

    One field, so a failed login cannot reveal which of the two exists.
    """

    identifier: str = Field(min_length=1, max_length=320, examples=["ernest_t"])
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: EmailStr
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _always_utc(cls, v: datetime) -> datetime:
        """Guarantee every timestamp we emit carries an explicit UTC offset.

        A driver that hands back a naive datetime would otherwise make the same
        account serialise with a trailing Z on register and without one on
        /auth/me, leaving the frontend to special case it.
        """
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class TokenPair(BaseModel):
    """Returned to non-browser callers.

    Browser clients get the same tokens as httpOnly cookies and can ignore the
    body. Service callers read `access_token` and send it as a bearer header.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access-token lifetime in seconds.")


class AuthResponse(BaseModel):
    """Returned by register, login and refresh alike, so the frontend parses one shape."""

    user: UserOut
    tokens: TokenPair


class MessageResponse(BaseModel):
    message: str
