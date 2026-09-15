"""Fixtures for driving the service layer directly, with no HTTP in the way."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from model.entities import User
from model.schemas import RegisterRequest
from service import auth_service


@pytest.fixture
def register_request(registration_payload: dict[str, str]) -> RegisterRequest:
    return RegisterRequest(**registration_payload)


@pytest.fixture
async def registered_user(session: AsyncSession, register_request: RegisterRequest) -> User:
    user, _ = await auth_service.register(session, register_request)
    return user


@pytest.fixture
async def another_administrator(session: AsyncSession) -> User:
    """A second administrator, so a demotion under test is not the last one.

    Needed by every demotion test since the last-administrator guard landed.
    Without one in the database, demoting anybody is refused — which is the
    point of the guard, and is asserted directly in
    `test_the_only_administrator_cannot_be_demoted_by_anyone`.
    """
    user = User(
        username="admin_spare",
        email="admin.spare@example.com",
        password_hash="not-a-real-hash",
        role=UserRole.ADMIN,
    )
    session.add(user)
    await session.commit()
    return user
