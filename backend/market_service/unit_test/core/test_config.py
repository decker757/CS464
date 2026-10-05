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


def test_the_liquidity_default_is_configured_and_has_a_value() -> None:
    """[1.2] #2: "b defaults to a configured value", and docs/api promises 100."""
    assert _settings().default_liquidity_b == Decimal("100")


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


# --- [3.4] #12 / [3.3] #11 dispute window -----------------------------------
# "The dispute window is `dispute_window_seconds`, an int with a 30-day
# ceiling, and the five-minute gap is a constant".
def test_the_dispute_window_defaults_to_twenty_four_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[3.3] #11: "configurable, with a 24-hour default"."""
    monkeypatch.delenv("DISPUTE_WINDOW_SECONDS", raising=False)
    _env(monkeypatch)

    assert Settings(_env_file=None).dispute_window_seconds == 86400


@pytest.mark.parametrize(("raw", "parsed"), [("1", 1), ("2592000", 2592000)])
def test_the_dispute_window_is_read_from_the_environment_as_whole_seconds(
    raw: str, parsed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One second is allowed, for a demo; exactly thirty days is the ceiling,
    inclusive. Fails with `lt` in place of `le`."""
    _env(monkeypatch, DISPUTE_WINDOW_SECONDS=raw)

    settings = Settings(_env_file=None)

    assert type(settings.dispute_window_seconds) is int
    assert settings.dispute_window_seconds == parsed


@pytest.mark.parametrize("bad", ["0", "-1", "2592001", "24h", "1.5"])
def test_a_nonsensical_dispute_window_refuses_to_boot(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero lets nobody send a result back; past thirty days is a mistyped unit;
    `24h` and `1.5` are not whole seconds, and a float field would take `1.5`."""
    _env(monkeypatch, DISPUTE_WINDOW_SECONDS=bad)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
