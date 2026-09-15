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
    """Same rule as the other four services, for the same two reasons.

    A default signing key is a published key, and here it would let anybody open
    a socket as anybody. A default bus URL is the subtler one: it is a
    credential the moment somebody puts a password in it, and a service that
    starts against the wrong Redis looks completely healthy while broadcasting
    nothing to anyone.

    The environment has to be emptied rather than the arguments omitted:
    pydantic-settings reads os.environ regardless of what the caller passes,
    and conftest has both of these set.
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
    """Unlike the two above, because neither is a credential.

    They are tuning, and the numbers that matter are relative: a subscription
    ceiling generous enough that no real client notices, and a queue deep
    enough to ride out a garbage collection but not a disconnection.
    """
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

    It matters more here than in the other four. Everywhere else this list only
    configures a middleware; here it is also the socket's origin allowlist, so a
    parse failure is a security control that never loads rather than a header
    that comes out wrong.
    """
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    settings = Settings(**_REQUIRED, _env_file=None)

    assert settings.cors_origins == ["http://a.test", "http://b.test"]
