"""Request and response contracts. Pure validation, no database.

These are the rules the frontend sees as 422s, so they are worth asserting
directly rather than only through a route.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from model.schemas import LoginRequest, RegisterRequest, UserOut
from unit_test.conftest import VALID_PASSWORD


def _payload(**overrides: object) -> dict[str, object]:
    return {
        "username": "ernest_t",
        "email": "ernest@example.com",
        "password": VALID_PASSWORD,
        **overrides,
    }


def test_a_valid_registration_parses() -> None:
    parsed = RegisterRequest(**_payload())

    assert parsed.username == "ernest_t"
    assert parsed.email == "ernest@example.com"


@pytest.mark.parametrize(
    "raw", ["ERNEST@Example.COM", "  ernest@example.com  ", "Ernest@Example.com"]
)
def test_email_is_normalised_to_lowercase(raw: str) -> None:
    """Uniqueness depends on this, so it is checked here rather than assumed."""
    assert RegisterRequest(**_payload(email=raw)).email == "ernest@example.com"


@pytest.mark.parametrize("username", ["has spaces", "has@symbol", "has.dot", "ab", "x" * 33])
def test_invalid_usernames_are_refused(username: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(**_payload(username=username))


@pytest.mark.parametrize("username", ["ernest_t", "ernest-t", "Ernest123", "a_b-c"])
def test_valid_usernames_are_accepted(username: str) -> None:
    assert RegisterRequest(**_payload(username=username)).username == username


def test_a_short_password_is_refused() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(**_payload(password="short"))


def test_password_length_follows_the_configured_minimum() -> None:
    """Exactly at the boundary must pass, one under must not."""
    from core.config import get_settings

    minimum = get_settings().password_min_length

    assert RegisterRequest(**_payload(password="p" * minimum)).password
    with pytest.raises(ValidationError):
        RegisterRequest(**_payload(password="p" * (minimum - 1)))


def test_a_malformed_email_is_refused() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(**_payload(email="not-an-email"))


def test_login_takes_one_identifier_field() -> None:
    """One field for username-or-email, so a failure cannot reveal which exists."""
    assert set(LoginRequest.model_fields) == {"identifier", "password"}


def test_user_output_carries_no_secret_fields() -> None:
    fields = set(UserOut.model_fields)

    assert "password" not in fields
    assert "password_hash" not in fields
    assert fields == {"id", "username", "email", "created_at"}


def test_a_naive_timestamp_is_stamped_as_utc() -> None:
    """Guards the contract that every timestamp we emit ends in Z."""
    out = UserOut(
        id=uuid.uuid4(),
        username="ernest_t",
        email="ernest@example.com",
        created_at=datetime(2026, 9, 13, 8, 0, 0),
    )

    assert out.created_at.tzinfo is not None
    assert out.model_dump_json().count("Z") == 1


def test_an_aware_timestamp_is_left_alone() -> None:
    when = datetime(2026, 9, 13, 8, 0, 0, tzinfo=UTC)

    out = UserOut(
        id=uuid.uuid4(), username="ernest_t", email="e@example.com", created_at=when
    )

    assert out.created_at == when
