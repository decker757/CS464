"""Settings, and which of them are allowed to have a default."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.config import Settings

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
