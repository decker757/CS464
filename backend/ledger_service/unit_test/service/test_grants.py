"""The starting mock-credit grant. [B-1] #32, [A-1] #29"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.database import get_session_factory
from model.entities import AccountKind, Entry, Transaction, TransactionKind
from service import accounts, grants, ledger_service

ZERO = Decimal(0)


@contextmanager
def starting_credits_of(amount: str) -> Iterator[Decimal]:
    """Run the block as if the deployment had been configured with `amount`.

    Through the environment and the settings cache rather than by patching
    `grants.get_settings`, because the deployment path is the one that breaks.
    A test that reached past the cache would still have passed before the fix
    this file's `test_lowering_...` cases exist for: the bug was that the
    configured value is consulted on a read at all, and only a test that makes
    the *settings* differ between two reads can see it.
    """
    before = os.environ.get("STARTING_CREDITS")
    os.environ["STARTING_CREDITS"] = amount
    get_settings.cache_clear()
    try:
        yield Decimal(amount)
    finally:
        if before is None:
            os.environ.pop("STARTING_CREDITS", None)
        else:
            os.environ["STARTING_CREDITS"] = before
        get_settings.cache_clear()


async def test_a_new_user_is_granted_on_first_read(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """"Each new account receives the configured fixed starting-credit amount."

    Nothing told this service the account was created. The auth service does
    not know that credits exist and there is no event between them, so the
    first read is what mints the grant.
    """
    balance = await ledger_service.balance_of_user(session, user_id)

    assert balance.amount == starting_credits


async def test_the_grant_is_an_append_only_ledger_entry(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """[B-1] #32's second criterion. Not a column, not a top-up: a real
    transaction, with both sides, that every later balance is derived from."""
    await grants.ensure_granted(session, user_id)

    account = await accounts.ensure(session, AccountKind.USER, user_id)
    entries = list(
        (
            await session.execute(select(Entry).where(Entry.account_id == account.id))
        ).scalars()
    )

    assert len(entries) == 1
    assert entries[0].amount == starting_credits
    assert entries[0].transaction.kind is TransactionKind.SIGNUP_GRANT


async def test_the_grant_moves_credits_rather_than_inventing_them(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """The platform is debited by exactly what the user is credited, which is
    what keeps the whole ledger at zero."""
    await grants.ensure_granted(session, user_id)

    platform = await accounts.ensure_platform(session)
    assert await accounts.balance_of(session, platform.id) == -starting_credits

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO


async def test_reading_twice_does_not_grant_twice(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """"Exactly once", and the reason a read is allowed to perform a write."""
    await ledger_service.balance_of_user(session, user_id)
    await ledger_service.balance_of_user(session, user_id)
    second = await ledger_service.balance_of_user(session, user_id)

    assert second.amount == starting_credits


async def test_the_key_is_the_user_id(user_id: uuid.UUID) -> None:
    """"Retrying account creation cannot produce multiple starting-credit
    grants" is true because there is nothing else the key could vary on."""
    assert grants.grant_key(user_id) == f"signup-grant:{user_id}"
    assert grants.grant_key(user_id) != grants.grant_key(uuid.uuid4())


async def test_concurrent_first_reads_grant_once(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """Two tabs, or a double-clicked login. Both find no entries, both mint,
    and the unique index on the key is what makes the loser a replay."""

    async def read_balance() -> Decimal:
        async with get_session_factory()() as s:
            return (await ledger_service.balance_of_user(s, user_id)).amount

    balances = await asyncio.gather(*(read_balance() for _ in range(6)))

    assert set(balances) == {starting_credits}

    granted = (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.idempotency_key == grants.grant_key(user_id))
        )
    ).scalar_one()
    assert granted == 1


async def test_two_users_are_granted_separately(
    session: AsyncSession,
    user_id: uuid.UUID,
    other_user_id: uuid.UUID,
    starting_credits: Decimal,
) -> None:
    first = await ledger_service.balance_of_user(session, user_id)
    second = await ledger_service.balance_of_user(session, other_user_id)

    assert first.account_id != second.account_id
    assert first.amount == second.amount == starting_credits


async def test_reading_history_also_grants(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Either read closes the window, so a client that renders a statement
    before a balance sees the grant rather than an empty account."""
    page = await ledger_service.history_for_user(session, user_id, limit=10)

    assert len(page.entries) == 1
    assert page.entries[0].transaction.kind is TransactionKind.SIGNUP_GRANT


async def test_lowering_starting_credits_leaves_an_existing_balance_readable(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """A settings change must not brick the endpoint a trader opens first.

    The regression this file exists for. `ensure_granted` used to hand the
    configured amount to `posting.post` on every read, so once the number
    changed, the fingerprint of the legs no longer matched the stored
    transaction and the replay check refused it — `IdempotencyKeyReused`, a
    permanent 409 on `/ledger/balances/me` for every user granted under the old
    value. Both this service's README and `core/config.py` promise that
    changing `STARTING_CREDITS` is safe for accounts that already have theirs.
    """
    granted = await ledger_service.balance_of_user(session, user_id)
    assert granted.amount == starting_credits

    with starting_credits_of("500"):
        after = await ledger_service.balance_of_user(session, user_id)

    assert after.amount == starting_credits
    assert after.account_id == granted.account_id


async def test_lowering_starting_credits_leaves_an_existing_history_readable(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """The other read that mints, and so the other one that used to 409."""
    await ledger_service.history_for_user(session, user_id, limit=10)

    with starting_credits_of("500"):
        page = await ledger_service.history_for_user(session, user_id, limit=10)

    assert len(page.entries) == 1
    assert page.entries[0].transaction.kind is TransactionKind.SIGNUP_GRANT


async def test_raising_starting_credits_does_not_top_anybody_up(
    session: AsyncSession, user_id: uuid.UUID, starting_credits: Decimal
) -> None:
    """The symmetric half, and the one that would be a bug about money.

    Nothing rewrites an entry, so a later, larger value must not reach an
    account that has already been granted — by topping it up, by re-granting,
    or by any other route. The user keeps what they were given.
    """
    await ledger_service.balance_of_user(session, user_id)

    with starting_credits_of("9000"):
        after = await ledger_service.balance_of_user(session, user_id)
        await ledger_service.history_for_user(session, user_id, limit=10)

    assert after.amount == starting_credits

    entries = (
        await session.execute(
            select(func.count()).select_from(Entry).where(
                Entry.account_id == after.account_id
            )
        )
    ).scalar_one()
    assert entries == 1


async def test_a_new_value_reaches_accounts_created_after_it(
    session: AsyncSession,
    user_id: uuid.UUID,
    other_user_id: uuid.UUID,
    starting_credits: Decimal,
) -> None:
    """"A new value reaches accounts created after it and no others."

    The point of the promise, rather than just its safety: the setting is still
    live. The two halves are asserted together so that a fix which froze the
    amount outright — never reading settings again — would fail here.
    """
    old = await ledger_service.balance_of_user(session, user_id)

    with starting_credits_of("500") as lowered:
        new = await ledger_service.balance_of_user(session, other_user_id)

    assert old.amount == starting_credits
    assert new.amount == lowered


async def test_the_whole_ledger_still_sums_to_zero_across_a_change(
    session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    """Two users granted different amounts, one platform account carrying both.

    The invariant does not care what the configured number was at any point:
    every grant is a movement, so the table sums to zero whatever the setting
    did in between.
    """
    await ledger_service.balance_of_user(session, user_id)

    with starting_credits_of("500"):
        await ledger_service.balance_of_user(session, other_user_id)

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO

    platform = await accounts.ensure_platform(session)
    circulating = await accounts.balance_of(session, platform.id)
    user_held = await accounts.balance_of(
        session, (await accounts.ensure(session, AccountKind.USER, user_id)).id
    )
    other_held = await accounts.balance_of(
        session, (await accounts.ensure(session, AccountKind.USER, other_user_id)).id
    )
    assert circulating == -(user_held + other_held)


async def test_a_read_after_the_grant_does_not_touch_the_write_path(
    session: AsyncSession, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mechanism, stated once so a refactor cannot quietly undo it.

    Every read after the first answers from the existing transaction and never
    proposes a new one. Asserting that directly is what stops the fix being
    re-broken by someone moving the check back inside `posting.post`, where the
    legs — and so the configured amount — have to be built before anything can
    be compared.
    """
    await ledger_service.balance_of_user(session, user_id)

    async def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("a read of an already-granted user reached posting.post")

    monkeypatch.setattr(grants.posting, "post", fail)

    assert (await ledger_service.balance_of_user(session, user_id)).amount > ZERO
