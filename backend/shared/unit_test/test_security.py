"""The token verifier, tested once, where it lives. [F-6] #76

Every service's trust model is this one function, so its behaviour is asserted
here directly and each service keeps only a test that its `core/security.py`
seam wires its own settings in. No settings, no database, no HTTP.

Minting is fine here and nowhere outside `unit_test/`: each service's minting
guard scans `shared/` for `jwt.encode` and skips test paths. ADR 0002.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from shared.roles import UserRole
from shared.security import TokenClaims, decode_access_token

# Generated per run rather than written down, so there is no key-shaped string
# in the repository for GitGuardian to flag.
_SECRET = secrets.token_urlsafe(48)
_ALGORITHM = "HS256"
_ISSUER = "cs464-auth"


def _decode(token: str) -> TokenClaims | None:
    return decode_access_token(
        token, secret=_SECRET, algorithm=_ALGORITHM, issuer=_ISSUER
    )


def _mint_token(
    user_id: uuid.UUID,
    role: UserRole = UserRole.ADMIN,
    *,
    username: str = "ernest_t",
    expires_in: int = 900,
    issuer: str | None = None,
    secret: str | None = None,
) -> str:
    """Sign a token the way the auth service does, for tests only.

    The overridable issuer and secret are what let a test prove the verifier
    rejects a token from a system it does not trust.
    """
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "username": username,
            "role": UserRole(role).value,
            "iss": issuer if issuer is not None else _ISSUER,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "jti": secrets.token_urlsafe(16),
        },
        secret if secret is not None else _SECRET,
        algorithm=_ALGORITHM,
    )


def _hand_rolled(**claims: object) -> str:
    """Sign a token with PyJWT directly, for shapes the auth service will not build.

    Carries no role claim unless one is passed.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid.uuid4()),
        "username": "ernest_t",
        "iss": _ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **claims,
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)


@pytest.mark.parametrize("role", list(UserRole))
def test_a_valid_token_yields_its_claims(role: UserRole) -> None:
    user_id = uuid.uuid4()

    claims = _decode(_mint_token(user_id, role, username="ihsan"))

    assert claims is not None
    assert claims.user_id == user_id
    assert claims.username == "ihsan"
    assert claims.role is role
    assert claims.is_admin is (role is UserRole.ADMIN)


def test_an_expired_token_is_rejected() -> None:
    token = _mint_token(uuid.uuid4(), expires_in=-1)

    assert _decode(token) is None


def test_a_token_signed_with_another_key_is_rejected() -> None:
    """The forgery case. A signature check that passed here would be the whole
    authorisation model gone."""
    token = _mint_token(uuid.uuid4(), secret="b" * 40)

    assert _decode(token) is None


def test_a_token_from_another_issuer_is_rejected() -> None:
    """Every service here shares one HS256 secret, so the signature alone does
    not say the token was minted for this system. ADR 0002."""
    token = _mint_token(uuid.uuid4(), issuer="somebody-else")

    assert _decode(token) is None


@pytest.mark.parametrize("token", ["", "not.a.token", "Bearer x"])
def test_malformed_tokens_are_rejected(token: str) -> None:
    assert _decode(token) is None


def test_an_unsigned_token_is_rejected() -> None:
    """alg=none is the classic JWT bypass. PyJWT refuses it because
    `algorithms` names HS256 explicitly, which is worth pinning down."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "username": "attacker",
            "role": "admin",
            "iss": _ISSUER,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        key="",
        algorithm="none",
    )

    assert _decode(token) is None


def test_a_token_with_no_role_claim_is_read_as_a_trader() -> None:
    """Fail closed. Covers a token minted before auth grew the claim. The token
    is still valid; it just carries no authority."""
    claims = _decode(_hand_rolled())

    assert claims is not None
    assert claims.role is UserRole.TRADER
    assert claims.is_admin is False


@pytest.mark.parametrize("role", ["super_admin", "ADMIN", "", None, 7, ["admin"]])
def test_an_unrecognised_role_is_read_as_a_trader(role: object) -> None:
    """A role only a newer auth deploy knows about must not grant authority,
    and must not reject an otherwise valid token either."""
    claims = _decode(_hand_rolled(role=role))

    assert claims is not None
    assert claims.role is UserRole.TRADER
    assert claims.is_admin is False


def test_a_token_with_no_subject_is_rejected() -> None:
    """Not merely anonymous: a claims object with no user_id would make every
    creator-scoped query match nothing, or worse, everything — and in the
    ledger, somebody's balance."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "username": "ernest_t",
            "role": "admin",
            "iss": _ISSUER,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        _SECRET,
        algorithm=_ALGORITHM,
    )

    assert _decode(token) is None


# ---------------------------------------------------------------------------
# seconds_until_expiry — only the realtime service's socket watchdog reads it
# ---------------------------------------------------------------------------


def test_seconds_until_expiry_counts_down() -> None:
    """The number the socket's expiry watchdog sleeps on."""
    claims = _decode(_mint_token(uuid.uuid4(), expires_in=900))

    assert claims is not None
    assert 890 < claims.seconds_until_expiry() <= 900


def test_seconds_until_expiry_floors_at_zero() -> None:
    """Never negative, because the expiry watchdog sleeps on it; this covers a
    token that expires between the decode and the watchdog starting."""
    already_gone = TokenClaims(
        user_id=uuid.uuid4(),
        username="ernest_t",
        role=UserRole.TRADER,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    assert already_gone.seconds_until_expiry() == 0.0


def test_seconds_until_expiry_takes_an_injected_clock() -> None:
    expires_at = datetime.now(UTC) + timedelta(seconds=300)
    claims = TokenClaims(
        user_id=uuid.uuid4(),
        username="ernest_t",
        role=UserRole.TRADER,
        expires_at=expires_at,
    )

    remaining = claims.seconds_until_expiry(now=expires_at - timedelta(seconds=42))

    assert remaining == pytest.approx(42.0)
