"""Fixtures for driving the service layer directly, with no HTTP in the way."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
