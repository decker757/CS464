"""[A-1] #29 registration rules, asserted against the service layer.

Starting credits are [B-1] #32 and belong to the ledger. A guard below keeps
this service out of that domain.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import security
from core.errors import DuplicateUser
from core.roles import UserRole
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

    assert [problem.field for problem in caught.value.problems] == [expected_clash]


async def test_a_duplicate_names_every_clashing_field(
    session: AsyncSession,
    registered_user: User,
    registration_payload: dict[str, str],
) -> None:
    """Both clashes are reported together, not one submission at a time."""
    second = RegisterRequest(**registration_payload)

    with pytest.raises(DuplicateUser) as caught:
        await auth_service.register(session, second)

    assert [problem.field for problem in caught.value.problems] == ["username", "email"]


async def test_a_lost_race_still_names_the_clashing_field(
    session: AsyncSession,
    registered_user: User,
    registration_payload: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The insert, not the pre-check, catches a concurrent registration.

    The pre-check is forced to miss once, which is exactly what losing the race
    looks like: the winning row is not visible when we look, and the unique
    index refuses the insert a moment later. The frontend still gets a field,
    so `details` is present on every duplicate rather than usually present.
    """
    real_taken_fields = auth_service._taken_fields
    calls = {"n": 0}

    async def _miss_once(*args: object, **kwargs: object) -> list[str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return []
        return await real_taken_fields(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(auth_service, "_taken_fields", _miss_once)

    with pytest.raises(DuplicateUser) as caught:
        await auth_service.register(session, RegisterRequest(**registration_payload))

    assert calls["n"] == 2
    assert [problem.field for problem in caught.value.problems] == ["username", "email"]


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


async def test_a_new_account_starts_as_a_trader(registered_user: User) -> None:
    """Registration never grants authority. Promotion is a deliberate UPDATE.

    Guards the obvious catastrophe in [1.1] #1: if the column defaulted the
    other way, anyone who signed up could create markets.
    """
    assert registered_user.role is UserRole.TRADER


async def test_the_issued_token_carries_the_role_on_the_row(
    session: AsyncSession, registered_user: User
) -> None:
    """A promoted user's next token must reflect the promotion.

    The market service has no other way to learn it, so a token minted from a
    stale in-memory role would leave an admin locked out of their own markets.
    """
    registered_user.role = UserRole.ADMIN
    await session.flush()

    pair = await auth_service.issue_tokens(session, registered_user)

    claims = security.decode_access_token(pair.access_token)
    assert claims is not None
    assert claims.role is UserRole.ADMIN


def test_register_takes_no_ledger_dependency() -> None:
    """Boundary guard for [B-1] #32.

    If a credit or ledger argument ever reappears on this signature, the ledger
    domain has leaked back into the auth service.
    """
    import inspect

    params = set(inspect.signature(auth_service.register).parameters)

    assert params == {"session", "data"}
