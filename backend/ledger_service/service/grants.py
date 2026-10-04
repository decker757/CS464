"""The starting mock-credit grant. [B-1] #32, [A-1] #29, ADR 0009.

Minted lazily, on a user's first read of their own balance, history or
portfolio, or their first trade, because this service is never told a
registration happened and the auth service does not know credits exist. ADR
0009 has the alternatives it rejected. An administrator's read of somebody
else's mints nothing (#188).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from model.entities import Account, AccountKind, TransactionKind
from service import accounts, posting
from service.posting import Leg

# Namespaced so it cannot collide with another key naming the same user. The
# user id is the whole key: one grant per user, ever.
_KEY_PREFIX = "signup-grant"


def grant_key(user_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}:{user_id}"


async def ensure_granted(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> Account:
    """Mint this user's starting credits unless they already have them, and
    return the user's account. Commits when it mints.

    Idempotent twice: the early lookup settles the usual case in one SELECT,
    and the unique key turns a racing first read into a replay.

    Read the amount only after that lookup misses. `post` refuses a key whose
    legs differ from the stored ones, so building them on every read would give
    every granted user `IdempotencyKeyReused` forever once `STARTING_CREDITS`
    changes. Only this caller knows a changed amount is an edit, not a bug.

    Two processes minting for one new user across a restart that changed the
    amount get one 409, which a refresh resolves.
    """
    user = await accounts.ensure(session, AccountKind.USER, user_id)

    if await posting.find_by_idempotency_key(session, grant_key(user_id)):
        return user

    amount = get_settings().starting_credits
    platform = await accounts.ensure_platform(session)

    await posting.post(
        session,
        idempotency_key=grant_key(user_id),
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[Leg(account=platform, amount=-amount), Leg(account=user, amount=amount)],
        context={"user_id": str(user_id)},
        now=now,
    )

    return user
