"""Password hashing, and minting and verifying tokens. [A-1..A-3]

Argon2 and token minting live only here. Verifying is `shared/security.py`'s,
bound to this service's settings below. ADR 0012.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from core.config import get_settings
from core.roles import UserRole
from shared import security as _shared
from shared.security import TokenClaims

# Argon2id with the argon2-cffi defaults, which track the OWASP recommendation.
_hasher = PasswordHasher()

# Login verifies against this for an unknown account, so timing does not
# reveal whether the account exists.
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
def create_access_token(user_id: uuid.UUID, username: str, role: UserRole) -> str:
    """Mint an access token.

    `role` has no default, so no call site can forget it and mint a token whose
    authority differs from the row.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "username": username,
        # ADR 0003: other services authorise from this claim alone.
        "role": UserRole(role).value,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenClaims | None:
    """Return the claims, or None for any malformed, expired or foreign token.

    Through `shared/security.py`, as every service does, so this one cannot
    drift from the rest. Minting stays here only. ADR 0012.
    """
    settings = get_settings()
    return _shared.decode_access_token(
        token,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
    )


# --------------------------------------------------------------------------
# Refresh tokens (opaque, stored hashed, revocable)
# --------------------------------------------------------------------------
# Logout revokes the refresh token, since an access token cannot be revoked
# (ADR 0002). Only its SHA-256 is stored, so a leaked table resumes nothing.
def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_refresh_token() -> tuple[str, str]:
    """Return (raw_token_for_the_client, hash_to_store)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)
