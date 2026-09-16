"""Settings parsing and the boundary this service is not allowed to cross."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.config import Settings


def _settings(**overrides: object) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://market_svc:x@localhost:5432/cs464",
        "jwt_secret": "a" * 32,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def _env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    """Set the two required settings in the environment, plus whatever a test
    is actually about.

    Used by every test that cares about parsing rather than about validation.
    A constructor kwarg skips the settings source entirely, so it proves the
    field parses and nothing about the path a deploy takes — which is the gap
    that let the CORS_ORIGINS decoding bug reach a container.
    """
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://market_svc:x@localhost:5432/cs464"
    )
    monkeypatch.setenv("JWT_SECRET", "a" * 32)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("missing", ["DATABASE_URL", "JWT_SECRET"])
def test_the_required_settings_have_no_default(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same rule as the auth service, for the same two reasons.

    A default database URL is a credential in the repository. A default signing
    key is a published key, and here it would mean anybody could mint
    themselves an admin token and start creating markets.

    The environment has to be emptied rather than the arguments omitted:
    pydantic-settings reads os.environ regardless of what the caller passes, and
    conftest has both of these set.
    """
    _env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_short_signing_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_secret="tooshort")


def test_cors_origins_parse_from_a_comma_separated_string() -> None:
    """Guards the deployment path, not just the default.

    pydantic-settings JSON-decodes a complex field straight from the
    environment before any validator runs, which is why cors_origins is
    annotated NoDecode. Without it this raises at import time and the container
    crash-loops while every defaults-only test still passes.
    """
    parsed = _settings(cors_origins="http://a.com, http://b.com")

    assert parsed.cors_origins == ["http://a.com", "http://b.com"]


def test_the_token_settings_default_to_the_auth_service_contract() -> None:
    """All three have to agree with the auth service or nothing validates."""
    parsed = _settings()

    assert parsed.jwt_algorithm == "HS256"
    assert parsed.jwt_issuer == "cs464-auth"
    assert parsed.access_cookie_name == "access_token"


def test_the_liquidity_default_is_configured_and_has_a_value() -> None:
    """[1.2] #2: "b defaults to a configured value and accepts override".

    Unlike DATABASE_URL and JWT_SECRET this one HAS a default, and the contrast
    is the point. Those two are refused without a value because a default is a
    credential; a liquidity parameter is not, and making it required would mean
    every teammate edits .env before compose will start in order to restate a
    number none of them disagrees about.
    """
    assert _settings().default_liquidity_b == Decimal("100")


def test_the_liquidity_default_can_be_overridden_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sets DEFAULT_LIQUIDITY_B rather than passing a keyword argument.

    A constructor kwarg skips the settings source entirely, so it proves the
    field parses and nothing about the path a deploy actually takes. That gap
    is how the CORS_ORIGINS decoding bug reached a container.
    """
    _env(monkeypatch, DEFAULT_LIQUIDITY_B="250")

    assert Settings(_env_file=None).default_liquidity_b == Decimal("250")


def test_the_liquidity_default_is_read_from_the_environment_as_a_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An environment variable is always a string; the field must not stay one.

    `"250" * 2` is `"250250"`, and this value is multiplied by a logarithm.
    """
    _env(monkeypatch, DEFAULT_LIQUIDITY_B="12.5")

    parsed = Settings(_env_file=None).default_liquidity_b

    assert isinstance(parsed, Decimal)
    assert parsed == Decimal("12.5")


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_a_non_positive_liquidity_default_refuses_to_boot(bad: str) -> None:
    """Fail at startup rather than writing an unpriceable market to every row.

    The per-market field is validated in model/schemas.py; this is the same
    rule for the value that applies when the admin does not choose one.
    """
    with pytest.raises(ValidationError):
        _settings(default_liquidity_b=bad)


# --- [F-4] #44 auto-close ---------------------------------------------------


def test_the_sweep_settings_have_working_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional, unlike the database URL and the signing key, because none of
    them is a credential and none of them is a correctness knob: a wrong
    interval makes a dashboard count stale, not a closed market tradeable."""
    for key in ("CLOSE_SWEEP_SECONDS", "CLOSE_SWEEP_BATCH", "CLOSE_SWEEP_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    _env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.close_sweep_seconds == 10.0
    assert settings.close_sweep_batch == 100
    assert settings.close_sweep_enabled is True


def test_the_sweep_interval_is_read_as_a_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An environment variable is always a string, and this one is handed to
    `asyncio.sleep`. A str would raise inside the background task, where the
    loop's own error handler would swallow it and retry forever."""
    _env(monkeypatch, CLOSE_SWEEP_SECONDS="2.5", CLOSE_SWEEP_BATCH="25")

    settings = Settings(_env_file=None)

    assert isinstance(settings.close_sweep_seconds, float)
    assert settings.close_sweep_seconds == 2.5
    assert isinstance(settings.close_sweep_batch, int)
    assert settings.close_sweep_batch == 25


@pytest.mark.parametrize("value", ["false", "False", "0"])
def test_the_sweeper_can_be_switched_off_from_the_environment(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spellings an operator or a compose file will actually write.

    Worth pinning, because every one of them is a non-empty string and a bool
    field that did not parse would read all three as True — leaving the
    sweeper running for someone who believed they had stopped it.
    """
    _env(monkeypatch, CLOSE_SWEEP_ENABLED=value)

    assert Settings(_env_file=None).close_sweep_enabled is False


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("CLOSE_SWEEP_SECONDS", "0"),
        ("CLOSE_SWEEP_SECONDS", "-1"),
        ("CLOSE_SWEEP_BATCH", "0"),
        ("CLOSE_SWEEP_BATCH", "-10"),
    ],
)
def test_a_nonsensical_sweep_setting_refuses_to_boot(
    key: str, bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero interval is a loop with no sleep in it, and a zero batch is a
    sweep that closes nothing while reporting success forever. Both fail at
    startup rather than at three in the morning."""
    _env(monkeypatch, **{key: bad})

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_no_authentication_policy_knobs_live_here() -> None:
    """Boundary guard: this service consumes identity, it does not define it.

    Password rules, token lifetimes and cookie flags belong to the auth
    service. A knob for one of them appearing here means a second, divergent
    opinion about a session has been created.
    """
    fields = set(Settings.model_fields)

    assert not {
        f
        for f in fields
        if "password" in f or "cookie_secure" in f or "ttl" in f or "refresh" in f
    }
