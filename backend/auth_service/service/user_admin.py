"""Administering other people's accounts. [4.4] #16

Separate from `auth_service.py`, which is about a user acting on their own
session — registering, logging in, refreshing, logging out. Everything here is
one user acting on another, which is the whole reason these operations are
audited and those are not.

HTTP is not mentioned in this file; failures are raised as domain errors and
`controller/errors.py` maps them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, raiseload

from core.errors import CannotChangeOwnRole, LastAdministrator, UserNotFound
from core.paging import decode_cursor, encode_cursor
from core.roles import UserRole
from model.audit import AdminAction
from model.entities import User
from service import audit
from service.audit import Actor


@dataclass(frozen=True)
class UserPage:
    """One page of the user list, newest registration first."""

    users: list[User]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


# Everything LIKE treats as a pattern, so that a query is matched as the text
# somebody typed. `_` is the case that makes this mandatory rather than tidy:
# usernames here are `[A-Za-z0-9_-]+` and half the team has one in theirs, so
# an unescaped search for `ernest_t` would also return `ernestXt`. The
# backslash has to be escaped first or it would escape the escapes.
_LIKE_WILDCARDS = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _contains(column: InstrumentedAttribute[str], needle: str) -> ColumnElement[bool]:
    """Case-insensitive substring match, with the pattern characters defanged.

    `ilike` rather than `lower(column) = ...`: an administrator investigating an
    anomaly has a fragment of a name from a support message, not the exact
    string, and making them type it in full is making them already know the
    answer.
    """
    return column.ilike(f"%{needle.translate(_LIKE_WILDCARDS)}%", escape="\\")


async def search_users(
    session: AsyncSession,
    *,
    query: str | None = None,
    limit: int,
    cursor: str | None = None,
) -> UserPage:
    """One page of accounts, newest first, narrowed to `query`. [4.1] #13.

    Deliberately **not audited**. This is the one administrative operation that
    decides nothing: an entry per search would bury the promotions and
    suspensions that the log exists to make findable, which is the argument
    ADR 0006 makes about draft autosave and CLAUDE.md states as "log the
    decision and never the keystrokes".

    `limit + 1` rows are fetched and the extra one is dropped, which is what
    makes `has_more` exact without a second COUNT — and a count is the wrong
    question anyway, because the answer is stale by the time it renders. Same
    approach as the audit feed and the ledger history.

    A blank or absent `query` lists everybody, so the page an administrator
    opens on is the user list rather than an empty state waiting to be typed
    into.

    **The match is a sequential scan and that is the accepted cost.** A
    `%fragment%` pattern cannot use the `lower(username)` indexes, which exist
    for uniqueness rather than for this, and no index shape helps an
    unanchored substring except a trigram one. At this scale the whole table is
    a page or two of memory; the upgrade, when it stops being, is
    `CREATE EXTENSION pg_trgm` and a GIN index on each column, which changes
    nothing above this function.
    """
    stmt = _ordered_query(query)

    if cursor is not None:
        # Raises MalformedCursor, which the controller turns into a 400.
        # Decoded before the query rather than let through to the database,
        # where it would surface as a driver error and a 500.
        created_at, row_id = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(User.created_at, User.id) < tuple_(created_at, row_id)
        )

    rows = list((await session.execute(stmt.limit(limit + 1))).scalars())

    if len(rows) <= limit:
        return UserPage(users=rows, next_cursor=None)

    page = rows[:limit]
    last = page[-1]
    return UserPage(users=page, next_cursor=encode_cursor(last.created_at, last.id))


def _ordered_query(query: str | None) -> Select[tuple[User]]:
    """Newest registration first, with the tie broken by id.

    The ordering is half of the keyset contract. `created_at` alone would leave
    two accounts registered in the same microsecond in an order Postgres is
    free to change between queries, which is exactly the case a cursor cannot
    survive.

    Username **or** email, because [4.1] #13 asks for one search box and an
    administrator holding one of the two should not have to say which it is.

    Sessions are refused rather than loaded, and it is not a micro-optimisation:
    `User.refresh_tokens` is `lazy="selectin"`, so without this every page of
    users also drags in every refresh token those accounts hold — a second
    query, unbounded in the rows it returns, for data no response here
    contains. This is the one place a `User` is loaded in bulk.

    `raiseload` rather than `noload` because the two fail differently. `noload`
    would hand a caller an empty collection for an account that has three live
    sessions, which is a wrong answer wearing the shape of a right one;
    `raiseload` says the attribute was not loaded. Nothing in this story wants
    them, and whatever does should say so in its own query.
    """
    stmt = select(User).options(raiseload(User.refresh_tokens))

    needle = (query or "").strip()
    if needle:
        stmt = stmt.where(
            or_(_contains(User.username, needle), _contains(User.email, needle))
        )

    return stmt.order_by(User.created_at.desc(), User.id.desc())


async def _administrators_for_update(session: AsyncSession) -> set[uuid.UUID]:
    """Every administrator who could actually act, rows locked for this transaction.

    The lock is the point; the ids are a by-product. Postgres re-evaluates a
    `FOR UPDATE` row against the current committed state after waiting for it,
    so a concurrent demotion that commits while this query is blocked drops
    that user out of the result rather than being missed. That is what turns
    two administrators demoting each other from a race into a queue: whichever
    arrives second sees the first's work and finds itself looking at the last
    administrator.

    **`is_suspended` is part of the predicate, and leaving it out is a bug this
    once had.** A suspended administrator is an administrator on paper and
    nobody at all in practice: `authenticate` and `get_current_user` both
    refuse the account before the role is ever consulted, so they cannot call
    the route that would undo any of this. Counting them makes the set look
    survivable when it is already empty, and the check passes while handing
    back a database only a manual UPDATE can rescue. What matters is who can
    still act, not who the column says is privileged.

    Taken on every path, not only a demotion, and that is about lock order
    rather than about the invariant. A promotion cannot empty the set, but it
    does need the target's row locked before the role is read: two
    administrators promoting the same trader in one instant both read TRADER,
    both write ADMIN, and both log a `trader → admin` that only one of them
    performed. The obvious shape — lock the target first, and take this set
    only once that read says demotion — deadlocks, because two administrators
    demoting each other then each hold their own target and each wait for the
    other's inside this query. So every request takes this set first and its
    target second, and two requests can only ever wait on each other in one
    direction. The cost is that role changes queue behind one another, and
    there are a handful of those a week.

    [4.2] #14 has to take this same lock when it suspends, for the same
    reason in the other direction: suspending the last unsuspended
    administrator empties the set without touching `role` at all, so nothing
    here can see it coming.
    """
    rows = await session.execute(
        select(User.id)
        .where(User.role == UserRole.ADMIN, User.is_suspended.is_(False))
        .with_for_update()
    )
    return set(rows.scalars())


async def change_role(
    session: AsyncSession,
    *,
    actor: Actor,
    target_id: uuid.UUID,
    role: UserRole,
    reason: str | None = None,
) -> User:
    """Move `target_id` to `role`, recording the decision in the same transaction.

    Returns the user as they now are. Idempotent: asking for the role someone
    already holds succeeds and writes nothing, because a request that changed
    nothing is not a decision and an audit log that collects them is a log
    nobody finishes reading. ADR 0006 makes the same argument about draft
    autosave.

    The UPDATE and the audit INSERT go to the database in one transaction and
    commit together, so there is no ordering in which this account gains
    administrative authority with nothing recording who granted it.

    Refuses to remove the last administrator. Two rules do that together, and
    neither is sufficient alone: an administrator may not change their own
    role, which bounds the sequential case, and a demotion locks the
    administrator rows, which bounds the concurrent one.

    Logged once, too. The target's row is locked before its role is read, so
    two requests for the same change queue rather than interleave, and the
    second finds the role already held and writes nothing.
    """
    # Before the lookup, not after. This is the invariant that stops the
    # administrator set from emptying — see CannotChangeOwnRole — and it should
    # not depend on the target row being readable for it to hold.
    if target_id == actor.id:
        raise CannotChangeOwnRole

    # The set first and the target second, always in that order — see
    # `_administrators_for_update` for why the order is what keeps two of
    # these from deadlocking. `populate_existing` so the role read below comes
    # from the locked row and never from an instance this session loaded
    # before it held the lock; the lock is what stops the answer changing
    # between the question and the decision.
    remaining = await _administrators_for_update(session)
    target = await session.get(
        User, target_id, with_for_update=True, populate_existing=True
    )
    if target is None:
        raise UserNotFound

    if target.role is UserRole.ADMIN and role is not UserRole.ADMIN:
        # A demotion. Discard rather than check membership. The target may
        # legitimately be absent from the locked set — a suspended
        # administrator is excluded from it by design — and that is not the
        # same fact as their having been demoted by somebody else a moment
        # ago. Conflating the two makes demoting a suspended administrator
        # silently do nothing, which is the one account most likely to need it.
        remaining.discard(target.id)

        if not remaining:
            raise LastAdministrator

    # Read after the guard, from the locked row, so the idempotent case below
    # sees what is actually there and a change that somebody else already
    # applied — a demotion or a promotion — is a no-op here rather than a
    # second entry claiming a change that did not happen.
    previous = target.role
    if previous is role:
        return target

    target.role = role

    await audit.record(
        session,
        actor=actor,
        action=AdminAction.USER_ROLE_CHANGED,
        target_type="user",
        target_id=target.id,
        # Snapshotted like the actor's, and for the same reason: `audit_svc`
        # holds no grant on auth.users, so an entry that named only an id would
        # be unreadable to the one role allowed to read it.
        target_label=target.username,
        reason=reason,
        context={"from": previous.value, "to": role.value},
    )

    await session.commit()
    return target
