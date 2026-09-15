"""The write path: double entry, idempotency and overdrafts. [F-1] #41"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    IdempotencyKeyReused,
    InsufficientFunds,
    UnbalancedTransaction,
)
from model.entities import Account, AccountKind, Entry, TransactionKind
from service import accounts, posting
from service.posting import Leg

ZERO = Decimal(0)


async def _pair(session: AsyncSession) -> tuple[Account, Account]:
    """A user account and the platform account, both empty."""
    user = await accounts.ensure(session, AccountKind.USER, uuid.uuid4())
    platform = await accounts.ensure_platform(session)
    return user, platform


def _key() -> str:
    """A key nothing else in this database has used."""
    return f"test:{uuid.uuid4()}"


async def _fund(session: AsyncSession, user, platform, amount: Decimal) -> None:
    await posting.post(
        session,
        idempotency_key=_key(),
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[Leg(account=platform, amount=-amount), Leg(account=user, amount=amount)],
    )


async def test_a_movement_writes_both_sides(session: AsyncSession) -> None:
    user, platform = await _pair(session)

    transaction = await posting.post(
        session,
        idempotency_key=_key(),
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[
            Leg(account=platform, amount=Decimal("-500")),
            Leg(account=user, amount=Decimal("500")),
        ],
    )

    written = list(
        (
            await session.execute(
                select(Entry).where(Entry.transaction_id == transaction.id)
            )
        ).scalars()
    )

    assert len(written) == 2
    assert sum(e.amount for e in written) == ZERO


async def test_a_balance_is_the_sum_of_its_entries(session: AsyncSession) -> None:
    """[B-1] #32's third criterion, and [4.1] #13's. There is no other way for
    this service to answer the question."""
    user, platform = await _pair(session)

    await _fund(session, user, platform, Decimal("500"))
    await _fund(session, user, platform, Decimal("250"))

    assert await accounts.balance_of(session, user.id) == Decimal("750")


async def test_the_platform_holds_minus_the_credits_in_circulation(
    session: AsyncSession,
) -> None:
    """What makes a grant a movement rather than an invention."""
    user, platform = await _pair(session)

    await _fund(session, user, platform, Decimal("500"))

    assert await accounts.balance_of(session, platform.id) == Decimal("-500")


async def test_the_whole_ledger_sums_to_zero(session: AsyncSession) -> None:
    """The invariant this service exists to hold, asserted at its widest."""
    user, platform = await _pair(session)

    await _fund(session, user, platform, Decimal("500"))
    await _fund(session, user, platform, Decimal("125.5000"))

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()

    assert total == ZERO


async def test_legs_that_do_not_balance_are_refused(session: AsyncSession) -> None:
    """Credits move; they are not created. A caller whose legs do not balance
    has a bug, and letting it through would leave a ledger that cannot prove
    anything about itself."""
    user, platform = await _pair(session)

    with pytest.raises(UnbalancedTransaction):
        await posting.post(
            session,
            idempotency_key=_key(),
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[
                Leg(account=platform, amount=Decimal("-500")),
                Leg(account=user, amount=Decimal("400")),
            ],
        )


async def test_a_single_leg_is_refused(session: AsyncSession) -> None:
    """One leg summing to zero is an amount of zero, which says nothing and
    would appear on somebody's statement as a transaction that did not happen."""
    user, _ = await _pair(session)

    with pytest.raises(UnbalancedTransaction):
        await posting.post(
            session,
            idempotency_key=_key(),
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[Leg(account=user, amount=ZERO)],
        )


async def test_nothing_is_written_when_a_transaction_is_refused(
    session: AsyncSession,
) -> None:
    user, platform = await _pair(session)
    key = _key()

    with pytest.raises(UnbalancedTransaction):
        await posting.post(
            session,
            idempotency_key=key,
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[
                Leg(account=platform, amount=Decimal("-500")),
                Leg(account=user, amount=Decimal("400")),
            ],
        )

    assert await accounts.balance_of(session, user.id) == ZERO


async def test_replaying_a_key_writes_nothing_and_returns_the_original(
    session: AsyncSession,
) -> None:
    """[T-2] #22's "retrying the same request cannot execute twice", and
    [B-1] #32's "retrying account creation cannot produce multiple grants"."""
    user, platform = await _pair(session)
    key = _key()
    legs = [
        Leg(account=platform, amount=Decimal("-500")),
        Leg(account=user, amount=Decimal("500")),
    ]

    first = await posting.post(
        session, idempotency_key=key, kind=TransactionKind.SIGNUP_GRANT, legs=legs
    )
    second = await posting.post(
        session, idempotency_key=key, kind=TransactionKind.SIGNUP_GRANT, legs=legs
    )

    assert first.id == second.id
    assert await accounts.balance_of(session, user.id) == Decimal("500")


async def test_a_replay_survives_a_different_leg_order(session: AsyncSession) -> None:
    """A retry should not depend on a caller assembling its list identically
    twice."""
    user, platform = await _pair(session)
    key = _key()

    first = await posting.post(
        session,
        idempotency_key=key,
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[
            Leg(account=platform, amount=Decimal("-500")),
            Leg(account=user, amount=Decimal("500")),
        ],
    )
    second = await posting.post(
        session,
        idempotency_key=key,
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[
            Leg(account=user, amount=Decimal("500")),
            Leg(account=platform, amount=Decimal("-500")),
        ],
    )

    assert first.id == second.id


async def test_reusing_a_key_for_different_money_is_refused(
    session: AsyncSession,
) -> None:
    """The bug the fingerprint exists to catch. Answering this with the
    original transaction would tell a caller their new trade succeeded and hand
    them somebody else's as proof."""
    user, platform = await _pair(session)
    key = _key()

    await posting.post(
        session,
        idempotency_key=key,
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[
            Leg(account=platform, amount=Decimal("-500")),
            Leg(account=user, amount=Decimal("500")),
        ],
    )

    with pytest.raises(IdempotencyKeyReused):
        await posting.post(
            session,
            idempotency_key=key,
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[
                Leg(account=platform, amount=Decimal("-900")),
                Leg(account=user, amount=Decimal("900")),
            ],
        )


async def test_a_user_cannot_be_overdrawn(session: AsyncSession) -> None:
    user, platform = await _pair(session)
    await _fund(session, user, platform, Decimal("100"))

    with pytest.raises(InsufficientFunds):
        await posting.post(
            session,
            idempotency_key=_key(),
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[
                Leg(account=user, amount=Decimal("-101")),
                Leg(account=platform, amount=Decimal("101")),
            ],
        )


async def test_an_overdraft_error_says_how_short(session: AsyncSession) -> None:
    """"Insufficient funds" alone is the least useful true sentence available
    to a trading service that has to explain a refused trade."""
    user, platform = await _pair(session)
    await _fund(session, user, platform, Decimal("100"))

    with pytest.raises(InsufficientFunds) as raised:
        await posting.post(
            session,
            idempotency_key=_key(),
            kind=TransactionKind.SIGNUP_GRANT,
            legs=[
                Leg(account=user, amount=Decimal("-150")),
                Leg(account=platform, amount=Decimal("150")),
            ],
        )

    assert raised.value.balance == Decimal("100")
    assert raised.value.required == Decimal("150")


async def test_spending_the_whole_balance_is_allowed(session: AsyncSession) -> None:
    """Zero is a legal balance. Only below it is not."""
    user, platform = await _pair(session)
    await _fund(session, user, platform, Decimal("100"))

    await posting.post(
        session,
        idempotency_key=_key(),
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[
            Leg(account=user, amount=Decimal("-100")),
            Leg(account=platform, amount=Decimal("100")),
        ],
    )

    assert await accounts.balance_of(session, user.id) == ZERO


async def test_the_platform_may_go_negative(session: AsyncSession) -> None:
    """The exemption is the point of the platform account: its balance is
    minus the credits in circulation."""
    user, platform = await _pair(session)

    await _fund(session, user, platform, Decimal("100000"))

    assert await accounts.balance_of(session, platform.id) < ZERO


async def test_amounts_are_rounded_to_the_stored_scale(session: AsyncSession) -> None:
    """A check performed against an unrounded value is a check against a number
    the database is about to change."""
    user, platform = await _pair(session)

    await posting.post(
        session,
        idempotency_key=_key(),
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[
            Leg(account=platform, amount=Decimal("-1.00005")),
            Leg(account=user, amount=Decimal("1.00005")),
        ],
    )

    assert await accounts.balance_of(session, user.id) == Decimal("1.0001")


async def test_an_account_with_no_entries_holds_zero(session: AsyncSession) -> None:
    """So a caller does not have to know whether a row exists before asking
    what is in it."""
    user, _ = await _pair(session)

    assert await accounts.balance_of(session, user.id) == ZERO
