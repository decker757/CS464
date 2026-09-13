"""Password hashing and token issuance/verification.

This is the ONLY module that knows how a password is hashed or how a token is
signed. Nothing else imports argon2 or jwt. Swapping the hash algorithm or
moving from HS256 to RS256 is a change confined to this file.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from core.config import get_settings
from core.roles import UserRole

# Argon2id with the argon2-cffi defaults, which track the OWASP recommendation.
_hasher = PasswordHasher()

# Verifying a throwaway hash costs the same as verifying a real one. Login uses
# this when the account does not exist so response time does not leak existence.
_DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def dummy_verify() -> None:
    """Burn the same CPU as a real verify, for the unknown-account path."""
    try:
        _hasher.verify(_DUMMY_HASH, "wrong")
    except VerifyMismatchError:
        pass


def needs_rehash(stored_hash: str) -> bool:
    """True once the stored hash predates the current Argon2 parameters."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


# --------------------------------------------------------------------------
# Access tokens (stateless JWT)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    username: str
    role: UserRole
    expires_at: datetime


def create_access_token(user_id: uuid.UUID, username: str, role: UserRole) -> str:
    """Mint an access token.

    `role` is required rather than defaulted. A default would let a new call
    site forget it and quietly mint a token whose authority does not match the
    row it was minted from.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "username": username,
        # Read by every other service to authorise admin-only routes. Services
        # cannot query auth.users across the schema boundary, so this claim is
        # the whole channel. See docs/adr/0003-market-service-boundary.md.
        "role": UserRole(role).value,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenClaims | None:
    """Return the claims, or None for any malformed, expired or foreign token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            username=payload["username"],
            role=_read_role(payload.get("role")),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def _read_role(raw: object) -> UserRole:
    """Fail closed: anything unrecognised is the least privileged role.

    Covers a token minted before the claim existed and a token carrying a role
    this build has never heard of, such as one issued by a newer deploy during
    a rollout. Neither is grounds for rejecting an otherwise valid token, but
    neither is grounds for granting authority either.
    """
    try:
        return UserRole(raw)
    except ValueError:
        return UserRole.TRADER


# --------------------------------------------------------------------------
# Refresh tokens (opaque, stored hashed, revocable)
# --------------------------------------------------------------------------
# The access token cannot be revoked before it expires, so logout works by
# revoking the refresh token: the session dies within one access-token TTL.
# Only the SHA-256 of the token is stored, so a database leak does not hand
# an attacker usable sessions.
def generate_refresh_token() -> tuple[str, str]:
    """Return (raw_token_for_the_client, hash_to_store)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
