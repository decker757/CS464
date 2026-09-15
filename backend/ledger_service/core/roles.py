"""The ledger's copy of the auth service's role vocabulary.

Deliberately duplicated rather than imported, exactly as `market_service` and
`audit_service` duplicate it. Services in this repository share no code and no
schema, and a package that all four imported would be the first thread tying
them together. The coupling that exists is the `role` claim in the access
token, which is a wire contract, and this file is this service's statement of
what it expects to find there.

ADR 0005 says a narrow `backend/shared/` should be extracted when this ticket
lands. It also names the blocker: each service builds from its own directory
with `COPY . .`, so a shared package is a Docker-context restructure rather
than a refactor. Until that happens, copying is not a choice.

Keep in step with `backend/auth_service/core/roles.py`. A value this file has
never heard of is treated as TRADER, so the two drifting apart costs authority
rather than granting it.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    TRADER = "trader"
    ADMIN = "admin"
