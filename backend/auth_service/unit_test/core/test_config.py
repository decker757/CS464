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


def test_a_short_ledger_service_key_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[L-1] #38, ADR 0021. A short key is a guessable one, and it opens a read
    of every username."""
    monkeypatch.setenv("LEDGER_SERVICE_KEY", "x" * 31)

    with pytest.raises(ValueError):
        Settings()


def test_a_missing_ledger_service_key_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Required, has no default ... or the service refuses to boot", in
    `docs/api/auth-service.md`. Catches a default being added later, which the
    length test alone would not: a default long enough to pass it is a
    published key, the reason JWT_SECRET has none either."""
    monkeypatch.delenv("LEDGER_SERVICE_KEY", raising=False)

    with pytest.raises(ValueError):
        Settings(_env_file=None)


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


# --- #307: a printed Settings carries no credential ---------------------------
def test_no_secret_appears_when_the_settings_are_printed() -> None:
    """#307. pytest prints a Settings in a failure message, and so does any log
    line or exception that formats one. The signing key, the database password
    and the ledger's key must appear in neither form, nor in a `model_dump()`,
    which `Field(repr=False)` would have left in clear."""
    signing_key = "signing-key-307-must-not-print"
    database_password = "db-password-307-must-not-print"
    ledger_key = "ledger-key-307-must-not-print-0123456789"
    settings = Settings(
        jwt_secret=signing_key,
        database_url=(
            f"postgresql+asyncpg://auth_svc:{database_password}@localhost:5432/cs464"
        ),
        ledger_service_key=ledger_key,
        _env_file=None,
    )

    for rendered in (repr(settings), str(settings), str(settings.model_dump())):
        for secret in (signing_key, database_password, ledger_key):
            assert secret not in rendered
