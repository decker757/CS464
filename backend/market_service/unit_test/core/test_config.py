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
    """Set the two required settings in the environment, plus a test's own.

    Parsing tests go through the environment because a constructor kwarg
    skips the path a deploy takes; that gap let the CORS_ORIGINS bug through.
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
    """A default would be a credential, or a published signing key, in the repo.

    The variable is deleted from the environment because conftest sets both.
    """
    _env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_short_signing_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_secret="tooshort")


def test_cors_origins_parse_from_a_comma_separated_string() -> None:
    """The field parses a comma-separated value. The next test guards `NoDecode`."""
    parsed = _settings(cors_origins="http://a.com, http://b.com")

    assert parsed.cors_origins == ["http://a.com", "http://b.com"]


def test_cors_origins_parse_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regression every service has had: without `NoDecode` a comma-separated
    CORS_ORIGINS is JSON-decoded before any validator and the container
    crash-loops. Only the environment path catches it."""
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    # No .env file, so a developer's local CORS_ORIGINS cannot mask the result.
    settings = _settings(_env_file=None)

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_the_token_settings_default_to_the_auth_service_contract() -> None:
    """All three have to agree with the auth service or nothing validates."""
    parsed = _settings()

    assert parsed.jwt_algorithm == "HS256"
    assert parsed.jwt_issuer == "cs464-auth"
    assert parsed.access_cookie_name == "access_token"


def test_the_liquidity_default_is_configured_and_has_a_value() -> None:
    """[1.2] #2: "b defaults to a configured value". Not a credential, so it has one."""
    assert _settings().default_liquidity_b == Decimal("100")


def test_the_liquidity_default_can_be_overridden_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the environment, the path a deploy takes; see `_env`."""
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
    """Fail at startup rather than give every new market an unpriceable `b`."""
    with pytest.raises(ValidationError):
        _settings(default_liquidity_b=bad)


# --- [F-4] #44 auto-close ---------------------------------------------------


def test_the_sweep_settings_have_working_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional: none is a credential or a correctness knob. ADR 0011."""
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
    """A str handed to `asyncio.sleep` would fail inside the sweeper, forever and quietly."""
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
    """Each is a non-empty string, which an unparsed bool would read as True."""
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
    """A zero interval never sleeps; a zero batch never closes anything."""
    _env(monkeypatch, **{key: bad})

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_no_authentication_policy_knobs_live_here() -> None:
    """Boundary guard: session policy belongs to the auth service alone."""
    fields = set(Settings.model_fields)

    assert not {
        f
        for f in fields
        if "password" in f or "cookie_secure" in f or "ttl" in f or "refresh" in f
    }
