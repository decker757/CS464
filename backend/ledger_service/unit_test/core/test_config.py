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
    """Same rule as the other three services, for the same two reasons.

    A default database URL is a credential in the repository. A default signing
    key is a published key, and here it would mean anybody could mint
    themselves a token and read anybody's balance.

    The environment has to be emptied rather than the arguments omitted:
    pydantic-settings reads os.environ regardless of what the caller passes,
    and conftest has both of these set.
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
    """Unlike the two above, because it is not a credential.

    A wrong database URL connects to the wrong database and a published signing
    key forges sessions. A different starting grant is a number nobody on this
    team disagrees about, and making it required would mean three people
    editing .env before compose will start in order to restate it.
    """
    assert Settings(**_REQUIRED, _env_file=None).starting_credits > 0


def test_starting_credits_must_be_positive() -> None:
    """Zero credits is an account that cannot trade; negative is an account
    that starts in debt to a market it has never seen."""
    with pytest.raises(ValidationError):
        Settings(**_REQUIRED, starting_credits=Decimal("0"), _env_file=None)


def test_starting_credits_is_a_decimal() -> None:
    """Not a float. It shares arithmetic with every Numeric column here, and
    a balance that has been through an IEEE double is a balance that can be
    wrong by an epsilon."""
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
    """The regression every service in this repository has already had, guarded
    on the path that actually breaks.

    Without `NoDecode`, pydantic-settings JSON-decodes a complex field straight
    from the environment *before* any validator runs, so a comma-separated
    CORS_ORIGINS raises at import time and the container crash-loops.

    The test above cannot catch that: a keyword argument comes from the init
    source, which never JSON-decodes, so it passes with or without the
    annotation. Only setting the environment variable exercises the deployment
    path — and CI does not set CORS_ORIGINS, so without this the guard would
    depend on whether the developer running the suite happens to have it in
    their .env.
    """
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    settings = Settings(**_REQUIRED, _env_file=None)

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


# =========================================================================
# REDIS_URL — the producer's bus. [F-9] #112
# =========================================================================
def test_redis_url_has_a_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`redis://redis:6379/0`, unlike `database_url` and the inherited secret.

    The environment has to be emptied rather than the argument omitted:
    pydantic-settings reads os.environ whatever the caller passes, and both CI
    and the repo-root .env set this one.

    `redis` is the hostname compose gives the bus on the shared network, the
    same shape `market_service_url` already uses for a service name.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)

    settings = Settings(**_REQUIRED, _env_file=None)

    assert settings.redis_url == "redis://redis:6379/0"


def test_the_app_boots_with_no_redis_url_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deploy check, on the path that actually breaks.

    The test above passes against a field that is required-with-a-fallback
    somewhere else in the stack; this one constructs the application the way
    `ci-backend.yml`'s "Verify the app boots" step does and is the assertion a
    missing default would fail.

    Note what this is *not* evidence for. The criterion justifies the default
    by claiming that step runs without `REDIS_URL`; it does not —
    `ci-backend.yml` sets `REDIS_URL: redis://localhost:6379/0` in the
    job-level `env:` block for all five matrix legs, so a required field would
    pass there and fail only in a checkout or a deploy that omitted it. The
    default is still right, for the reason `realtime_service` refuses one and
    this service takes one: a wrong Redis there is a relay that reports healthy
    and broadcasts nothing, and here it is one lost frame on a screen that
    reconciles on its next snapshot.

    `get_settings` is `lru_cache`d and conftest has already populated it, so
    the cache is cleared on the way in and again on the way out — otherwise
    every later test in the process reads a Settings built without this
    variable.
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
    """A default that cannot be overridden is a hardcoded value with a docstring.

    Set through the environment rather than as a keyword, for the reason
    `test_cors_origins_parse_from_the_environment` records: the init source is
    not the source a deployment uses.
    """
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/3")

    assert Settings(**_REQUIRED, _env_file=None).redis_url == "redis://localhost:6379/3"
