"""Settings parsing, and the boundary this service is not allowed to cross."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import Settings


def _settings(**overrides: object) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://audit_svc:x@localhost:5432/cs464",
        "jwt_secret": "a" * 32,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize("missing", ["DATABASE_URL", "JWT_SECRET"])
def test_the_required_settings_have_no_default(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default would be a credential, or a published signing key, in the repo.

    The variable is deleted from the environment because pydantic-settings
    reads os.environ whatever the caller passes, and conftest sets both.
    """
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://audit_svc:x@localhost:5432/cs464"
    )
    monkeypatch.setenv("JWT_SECRET", "a" * 32)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_short_jwt_secret_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_secret="too-short")


def test_cors_origins_parses_a_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The form docker-compose supplies. Without NoDecode on the field,
    pydantic-settings JSON-parses it first and the container never starts."""
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com,https://admin.example.com")

    assert Settings().cors_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


def test_cors_origins_tolerates_spacing_and_trailing_commas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", " https://a.example.com , https://b.example.com , ")

    assert Settings().cors_origins == ["https://a.example.com", "https://b.example.com"]


@pytest.mark.parametrize("field", ["default_page_size", "max_page_size"])
def test_a_page_size_of_zero_is_refused(field: str) -> None:
    """A zero ceiling would make every page empty and every cursor useless."""
    with pytest.raises(ValidationError):
        _settings(**{field: 0})


def test_settings_carry_nothing_that_would_let_this_service_write() -> None:
    """Boundary guard: a credit or market setting here means a second job."""
    fields = set(Settings.model_fields)

    assert not {
        f for f in fields if "credit" in f or "balance" in f or "liquidity" in f
    }
