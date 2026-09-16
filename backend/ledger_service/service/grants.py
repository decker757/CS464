"""The starting mock-credit grant. [B-1] #32, and [A-1] #29's fifth criterion.

The grant is minted **here**, lazily, the first time anything asks what a user
holds. Not at registration, because this service is never told a registration
happened: the auth service owns users and credentials and does not know that
credits exist — `test_registration.py` has a guard that fails if the word
appears in a response — and there is no event, queue or broker between the two.

`backend/auth_service/README.md` settled this before any of it was built, and
the argument is worth repeating because the obvious alternatives all look
cheaper than they are:

- **A balance column topped up at signup.** Puts the grant in a column and
  every later movement in ledger entries, so the two never reconcile, and
  breaks [B-1]'s own rule that a balance is the sum of a user's entries. It
  also puts credits back inside the auth service.
- **An outbox, or an event.** Registration commits a user row and an event row;
  something relays it. Real plumbing, and it leaves a window in which a new
  account's balance is observably wrong.
- **A shared transaction across both services.** Satisfies [B-1]'s "atomically"
  literally, by deleting the boundary that makes these separate services.

Lazily minting has none of that. A user with no entries is by definition a user
who has not been granted yet, so the first read writes the genesis transaction
and carries on. The key is the user id, so retrying registration cannot
double-grant and two concurrent first requests race safely. There is no window
where a balance is wrong, because the read that would have observed the window
is the read that closes it.

The one oddity is that a read performs a write. That is normal for a welcome
grant and costs one insert per user, ever.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from model.entities import Account, AccountKind, TransactionKind
from service import accounts, posting
from service.posting import Leg

# Namespaced, like every other idempotency key this system will generate, so
# that `signup-grant:<uuid>` cannot collide with a trade keyed on the same
# user. The user id is the whole key: one user, one grant, forever.
_KEY_PREFIX = "signup-grant"


def grant_key(user_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}:{user_id}"


async def ensure_granted(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> Account:
    """Mint this user's starting credits, unless they already have them, and
    return the account they landed in.

    Returning the account rather than nothing is what stops every read costing
    an extra SELECT: this function has already resolved it, and a caller that
    had to look it up again would be asking for a row it was just handed.

    Idempotent twice over, which is deliberate: the early return below settles
    the ordinary case in one SELECT, and the unique index on `idempotency_key`
    settles the racing one — so this is comfortable to call on every single
    read, which is the only reason a read is allowed to perform a write at all.

    Debits the platform and credits the user, so the grant is a movement
    between two accounts rather than credits appearing from nowhere. That is
    what keeps `SUM(amount)` over the whole ledger at exactly zero, and it is
    the assertion in `unit_test/service/test_concurrency.py` that would catch
    almost any mistake in this file.

    **The configured amount is read only when a grant is actually minted**, and
    the early return below is what makes that true. `post` fingerprints the legs
    it is handed and refuses a key that already names a *different* movement —
    right for a trade, where the key is derived from the request and a mismatch
    is a caller bug, and wrong here, where the key is the user id and the amount
    is a setting an administrator may edit. Build the legs on every read and
    lowering `STARTING_CREDITS` gives every already-granted user
    `IdempotencyKeyReused` on their own balance, for ever, from a change both
    the README and `core/config.py` promise is safe. Only the caller can tell a
    mistake from an edit, which is why the rule is here and not in `post`.

    Two concurrent *first* reads still both mint, and the unique index makes the
    loser a replay; they agree on the amount because they read one process's
    settings. The residual window is two processes minting for one brand-new
    user across a restart that changed the value — one 409, resolved by a
    refresh.
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
