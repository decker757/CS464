"""[A-1] #29 registration rules, asserted against the service layer.

Starting credits are [B-1] #32 and belong to the ledger. A guard below keeps
this service out of that domain.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import DuplicateUser
from model.entities import User
from model.schemas import RegisterRequest
from service import auth_service
from unit_test.conftest import VALID_PASSWORD


async def test_register_creates_exactly_one_account(
    session: AsyncSession, register_request: RegisterRequest
) -> None:
    user, _ = await auth_service.register(session, register_request)

    count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    assert count == 1
    assert user.username == "ernest_t"


async def test_the_password_is_stored_only_as_an_argon2_hash(
    session: AsyncSession, registered_user: User
) -> None:
    stored = (await session.execute(select(User.password_hash))).scalar_one()

    assert stored != VALID_PASSWORD
    assert VALID_PASSWORD not in stored
    assert stored.startswith("$argon2id$")


async def test_register_opens_a_first_session(
    session: AsyncSession, register_request: RegisterRequest
) -> None:
    _, tokens = await auth_service.register(session, register_request)

    assert tokens.access_token
    assert tokens.refresh_token
    assert tokens.expires_in > 0


@pytest.mark.parametrize(
    ("field", "value", "expected_clash"),
    [
        ("email", "someone.else@example.com", "username"),
        ("username", "someone_else", "email"),
    ],
)
async def test_a_duplicate_is_refused_and_names_the_clashing_field(
    session: AsyncSession,
    registered_user: User,
    registration_payload: dict[str, str],
    field: str,
    value: str,
    expected_clash: str,
) -> None:
    second = RegisterRequest(**{**registration_payload, field: value})

    with pytest.raises(DuplicateUser) as caught:
        await auth_service.register(session, second)

    assert caught.value.field == expected_clash


@pytest.mark.parametrize("variant", ["Ernest_T", "ERNEST_T"])
async def test_username_uniqueness_ignores_case(
    session: AsyncSession,
    registered_user: User,
    registration_payload: dict[str, str],
    variant: str,
) -> None:
    clashing = RegisterRequest(
        **{**registration_payload, "username": variant, "email": "other@example.com"}
    )

    with pytest.raises(DuplicateUser):
        await auth_service.register(session, clashing)


async def test_email_uniqueness_ignores_case(
    session: AsyncSession, registered_user: User, registration_payload: dict[str, str]
) -> None:
    clashing = RegisterRequest(
        **{**registration_payload, "username": "other_user", "email": "ERNEST@example.com"}
    )

    with pytest.raises(DuplicateUser):
        await auth_service.register(session, clashing)


async def test_a_retry_leaves_exactly_one_account(
    session: AsyncSession, register_request: RegisterRequest
) -> None:
    await auth_service.register(session, register_request)

    with pytest.raises(DuplicateUser):
        await auth_service.register(session, register_request)

    count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    assert count == 1


async def test_a_new_account_starts_unsuspended(registered_user: User) -> None:
    assert registered_user.is_suspended is False


def test_register_takes_no_ledger_dependency() -> None:
    """Boundary guard for [B-1] #32.

    If a credit or ledger argument ever reappears on this signature, the ledger
    domain has leaked back into the auth service.
    """
    import inspect

    params = set(inspect.signature(auth_service.register).parameters)

    assert params == {"session", "data"}
