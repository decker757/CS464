"""Settings parsing, through the environment as the container reads it.

A misparsed setting crashes the container at import, where no route test sees it.
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


def test_a_short_jwt_secret_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail at startup rather than signing tokens with a guessable key."""
    monkeypatch.setenv("JWT_SECRET", "too-short")

    with pytest.raises(ValueError):
        Settings()


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
