"""Settings, and which of them are allowed to have a default."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import Settings

_REQUIRED = {
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "0123456789abcdef0123",
}


@pytest.mark.parametrize("missing", ["REDIS_URL", "JWT_SECRET"])
def test_the_required_settings_have_no_default(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default signing key is a published key; a default Redis URL starts a
    service that relays nothing (D-046).

    The variable is deleted, not the argument omitted: pydantic-settings reads
    os.environ regardless, and conftest sets both.
    """
    monkeypatch.setenv("REDIS_URL", _REQUIRED["redis_url"])
    monkeypatch.setenv("JWT_SECRET", _REQUIRED["jwt_secret"])
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_a_short_signing_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, "jwt_secret": "short"}, _env_file=None)


def test_the_connection_limits_have_defaults() -> None:
    """Unlike the two above: these are tuning, not credentials."""
    settings = Settings(**_REQUIRED, _env_file=None)

    assert settings.max_subscriptions_per_connection > 0
    assert settings.send_queue_size > 0


@pytest.mark.parametrize(
    "field", ["max_subscriptions_per_connection", "send_queue_size"]
)
def test_the_connection_limits_must_be_positive(field: str) -> None:
    """Zero either way is a service that accepts sockets and never uses them:
    no market may be watched, or no frame may be queued to send."""
    with pytest.raises(ValidationError):
        Settings(**{**_REQUIRED, field: 0}, _env_file=None)


def test_cors_origins_parse_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: without `NoDecode` a comma-separated CORS_ORIGINS fails to
    parse from the environment and the container crash-loops. Here the list is
    also the socket's origin allowlist.
    """
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    settings = Settings(**_REQUIRED, _env_file=None)

    assert settings.cors_origins == ["http://a.test", "http://b.test"]
