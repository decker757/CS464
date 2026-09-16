"""Settings parsing.

These exercise the environment-variable path rather than the defaults. A
misparsed setting crashes the container at import time, which no request-level
test can catch, so the parsing itself needs direct coverage.
"""

from __future__ import annotations

import pytest

from core.config import Settings


def test_cors_origins_parses_a_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The form docker-compose and the deploy pipeline actually supply."""
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


def test_cors_origins_accepts_a_single_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://only.example.com")

    assert Settings().cors_origins == ["https://only.example.com"]


def test_cors_origins_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    assert "http://localhost:5173" in Settings().cors_origins


def test_a_short_jwt_secret_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail at startup rather than signing tokens with a guessable key."""
    monkeypatch.setenv("JWT_SECRET", "too-short")

    with pytest.raises(ValueError):
        Settings()


def test_numeric_settings_come_through_as_integers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "300")
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "16")

    settings = Settings()

    assert settings.access_token_ttl_seconds == 300
    assert settings.password_min_length == 16


def test_the_page_size_ceiling_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[4.1] #13's user list. The environment path, not the defaults — a
    misparsed setting crashes the container at import time."""
    monkeypatch.setenv("DEFAULT_PAGE_SIZE", "10")
    monkeypatch.setenv("MAX_PAGE_SIZE", "25")

    settings = Settings()

    assert settings.default_page_size == 10
    assert settings.max_page_size == 25


@pytest.mark.parametrize("field", ["DEFAULT_PAGE_SIZE", "MAX_PAGE_SIZE"])
def test_a_page_size_of_zero_is_refused(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """A zero ceiling would make every page empty and every cursor useless."""
    monkeypatch.setenv(field, "0")

    with pytest.raises(ValueError):
        Settings()


def test_settings_carry_nothing_from_another_domain() -> None:
    """Boundary guard: no credit, balance or trading knobs belong in auth."""
    fields = set(Settings.model_fields)

    assert not {f for f in fields if "credit" in f or "balance" in f or "market" in f}
