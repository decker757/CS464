"""Settings, and which of them are allowed to have a default."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.config import Settings, get_settings

_REQUIRED = {
    "database_url": "postgresql+asyncpg://ledger_svc:pw@localhost:5432/cs464",
    "jwt_secret": "0123456789abcdef0123",
}


@pytest.mark.parametrize("missing", ["DATABASE_URL", "JWT_SECRET"])
def test_the_required_settings_have_no_default(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default database URL is a credential in the repository, and a default
    signing key is a published one.

    The environment is emptied rather than the arguments omitted:
    pydantic-settings reads os.environ whatever the caller passes.
    """
    monkeypatch.setenv("DATABASE_URL", _REQUIRED["database_url"])
    monkeypatch.setenv("JWT_SECRET", _REQUIRED["jwt_secret"])
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_short_signing_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, "jwt_secret": "short"}, _env_file=None)


def test_starting_credits_has_a_default() -> None:
    """Unlike the two above, because it is not a credential."""
    assert Settings(**_REQUIRED, _env_file=None).starting_credits > 0


def test_starting_credits_must_be_positive() -> None:
    """Zero credits is an account that cannot trade; negative is an account
    that starts in debt to a market it has never seen."""
    with pytest.raises(ValidationError):
        Settings(**_REQUIRED, starting_credits=Decimal("0"), _env_file=None)


def test_starting_credits_is_a_decimal() -> None:
    """Not a float: it shares arithmetic with every Numeric column here."""
    settings = Settings(**_REQUIRED, starting_credits="1000.5000", _env_file=None)

    assert settings.starting_credits == Decimal("1000.5000")
    assert isinstance(settings.starting_credits, Decimal)


def test_cors_origins_accepts_a_comma_separated_value() -> None:
    """The validator, given the string form directly."""
    settings = Settings(
        **_REQUIRED, cors_origins="http://a.test, http://b.test", _env_file=None
    )

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_parse_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression every service has had, on the path that breaks.

    Without `NoDecode`, pydantic-settings JSON-decodes the variable before any
    validator runs and the container crash-loops. A keyword argument never
    takes that path, so only the environment exercises it.
    """
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    settings = Settings(**_REQUIRED, _env_file=None)

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


# =========================================================================
# REDIS_URL — the producer's bus. [F-9] #112
# =========================================================================
def test_redis_url_has_a_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`redis://redis:6379/0`, unlike `database_url` and the secret (D-046).

    The environment is emptied because pydantic-settings reads it whatever the
    caller passes, and CI and the repo-root .env both set this one.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)

    settings = Settings(**_REQUIRED, _env_file=None)

    assert settings.redis_url == "redis://redis:6379/0"


def test_the_app_boots_with_no_redis_url_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app constructs with no REDIS_URL in the environment (D-046). CI
    always sets REDIS_URL, so its boot step cannot catch a missing default.

    `get_settings` is cached, so it is cleared on the way in and out, or every
    later test reads a Settings built without this variable.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    get_settings.cache_clear()

    try:
        from main import create_app  # noqa: PLC0415

        app = create_app()
    finally:
        get_settings.cache_clear()

    assert app is not None


def test_a_configured_redis_url_wins_over_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default that cannot be overridden is a hardcoded value. Set through the
    environment, the source a deployment uses."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/3")

    assert Settings(**_REQUIRED, _env_file=None).redis_url == "redis://localhost:6379/3"
