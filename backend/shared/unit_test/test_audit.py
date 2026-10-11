"""The one `Actor` builder the token-authorised services share. [3.4] #12

`actor_of` is what puts a person's name and role in the audit log, so the role
must land as its wire value ("admin"), the string `auth.users.role` stores.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from shared.audit import Actor, actor_of
from shared.roles import UserRole
from shared.security import TokenClaims


def test_actor_of_copies_id_username_and_the_role_value():
    claims = TokenClaims(
        user_id=uuid.uuid4(),
        username="ernest_t",
        role=UserRole.ADMIN,
        expires_at=datetime.now(UTC),
    )

    actor = actor_of(claims)

    assert actor == Actor(id=claims.user_id, username="ernest_t", role="admin")
    assert type(actor.role) is str
