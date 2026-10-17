"""[A-2] #30 authentication rules, asserted against the service layer."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AccountSuspended, InvalidCredentials
from model.entities import User
from service import auth_service
from unit_test.conftest import VALID_PASSWORD


@pytest.mark.parametrize(
    "identifier", ["ernest_t", "ernest@example.com", "ERNEST_T", "  ernest_t  "]
)
async def test_login_accepts_username_or_email_in_any_case(
    session: AsyncSession, registered_user: User, identifier: str
) -> None:
    found = await auth_service.authenticate(session, identifier, VALID_PASSWORD)

    assert found.id == registered_user.id


async def test_a_wrong_password_is_refused(
    session: AsyncSession, registered_user: User
) -> None:
    with pytest.raises(InvalidCredentials):
        await auth_service.authenticate(session, "ernest_t", "wrong-password-here")


async def test_an_unknown_account_raises_the_same_error_as_a_wrong_password(
    session: AsyncSession, registered_user: User
) -> None:
    """Same exception type, so nothing downstream can tell the two apart."""
    with pytest.raises(InvalidCredentials):
        await auth_service.authenticate(session, "nobody_at_all", "wrong-password-here")


async def test_a_suspended_account_is_refused(
    session: AsyncSession, registered_user: User
) -> None:
    registered_user.is_suspended = True
    await session.commit()

    with pytest.raises(AccountSuspended):
        await auth_service.authenticate(session, "ernest_t", VALID_PASSWORD)


async def test_suspension_is_not_revealed_before_the_password_is_proven(
    session: AsyncSession, registered_user: User
) -> None:
    """Otherwise suspended accounts could be enumerated without credentials."""
    registered_user.is_suspended = True
    await session.commit()

    with pytest.raises(InvalidCredentials):
        await auth_service.authenticate(session, "ernest_t", "wrong-password-here")


async def test_login_loads_no_refresh_tokens_while_it_holds_the_user_lock(
    registered_user: User,
) -> None:
    """The lock is held until the login commits, so nothing slow belongs under
    it. `User.refresh_tokens` is `lazy="selectin"`, and an active user gains a
    token every refresh, so `lock_user` must not reload them. Review of #14."""
    from sqlalchemy import event  # noqa: PLC0415

    from core.database import get_engine, get_session_factory  # noqa: PLC0415

    statements: list[str] = []

    def keep(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", keep)
    try:
        async with get_session_factory()() as fresh:
            await auth_service.authenticate(fresh, "ernest_t", VALID_PASSWORD)
    finally:
        event.remove(engine, "before_cursor_execute", keep)

    locked_at = next(i for i, sql in enumerate(statements) if "FOR UPDATE" in sql)
    under_the_lock = statements[locked_at:]
    assert not [sql for sql in under_the_lock if "auth.refresh_tokens" in sql]
