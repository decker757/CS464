"""Finding an account to investigate, without HTTP. [4.1] #13

Users are inserted directly, at timestamps each test controls; nothing logs in.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MalformedCursor
from core.paging import encode_cursor
from core.roles import UserRole
from model.entities import User
from service import user_admin

# Fixed rather than `now`, so the asserted order does not race the clock.
_EPOCH = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


async def _make(
    session: AsyncSession,
    username: str,
    email: str,
    *,
    age: int = 0,
    suspended: bool = False,
    role: UserRole = UserRole.TRADER,
) -> User:
    """One account, registered `age` seconds after `_EPOCH`."""
    user = User(
        username=username,
        email=email,
        password_hash="not-a-real-hash",
        is_suspended=suspended,
        role=role,
        created_at=_EPOCH + timedelta(seconds=age),
    )
    session.add(user)
    await session.commit()
    return user


async def _team(session: AsyncSession) -> list[User]:
    """The three of us, oldest first."""
    return [
        await _make(session, "ernest_t", "ernest@example.com", age=1),
        await _make(session, "ihsan_k", "ihsan@company.co", age=2),
        await _make(session, "michelle_l", "michelle@example.com", age=3),
    ]


async def _search(session: AsyncSession, **kwargs) -> list[str]:
    """The usernames a search returns, in the order it returns them."""
    kwargs.setdefault("limit", 50)
    page = await user_admin.search_users(session, **kwargs)
    return [user.username for user in page.users]


# --- matching -------------------------------------------------------------


async def test_a_username_fragment_finds_the_account(session: AsyncSession) -> None:
    await _team(session)

    assert await _search(session, query="ernes") == ["ernest_t"]


async def test_an_email_fragment_finds_the_account(session: AsyncSession) -> None:
    """One search box for both username and email."""
    await _team(session)

    assert await _search(session, query="company.co") == ["ihsan_k"]


async def test_the_match_is_case_insensitive(session: AsyncSession) -> None:
    """Names are stored as typed; the searcher did not see them typed."""
    await _make(session, "Ernest_T", "Ernest@Example.com")

    assert await _search(session, query="ERNEST") == ["Ernest_T"]
    assert await _search(session, query="ernest") == ["Ernest_T"]


async def test_a_fragment_matching_nobody_finds_nobody(session: AsyncSession) -> None:
    await _team(session)

    assert await _search(session, query="nobody-by-that-name") == []


async def test_an_absent_query_lists_everybody(session: AsyncSession) -> None:
    """The page opens on the user list, not an empty state."""
    await _team(session)

    assert len(await _search(session)) == 3


async def test_a_blank_query_lists_everybody(session: AsyncSession) -> None:
    """A cleared search box sends `?q=`, which means everybody again."""
    await _team(session)

    assert len(await _search(session, query="   ")) == 3


# --- the query is text, not a pattern -------------------------------------


async def test_an_underscore_is_matched_literally(session: AsyncSession) -> None:
    """`_` is a LIKE wildcard and legal in usernames: the reason for escaping."""
    await _make(session, "ernest_t", "ernest@example.com", age=1)
    await _make(session, "ernestXt", "other@example.com", age=2)

    assert await _search(session, query="ernest_t") == ["ernest_t"]


async def test_a_percent_is_matched_literally(session: AsyncSession) -> None:
    """The other wildcard must not match the whole table."""
    await _team(session)

    assert await _search(session, query="%") == []


async def test_a_backslash_is_matched_literally(session: AsyncSession) -> None:
    """The escape character itself: finds nothing rather than a 500."""
    await _team(session)

    assert await _search(session, query="\\") == []


# --- ordering and paging --------------------------------------------------


async def test_the_newest_registration_comes_first(session: AsyncSession) -> None:
    await _team(session)

    assert await _search(session) == ["michelle_l", "ihsan_k", "ernest_t"]


async def test_a_suspended_account_is_still_listed(session: AsyncSession) -> None:
    """Unlike `_lock_administrators`: a suspended account is often the one sought."""
    await _make(session, "suspended_one", "suspended@example.com", suspended=True)

    assert await _search(session) == ["suspended_one"]


async def test_paging_returns_each_account_once(session: AsyncSession) -> None:
    await _team(session)

    first = await user_admin.search_users(session, limit=2)
    second = await user_admin.search_users(session, limit=2, cursor=first.next_cursor)

    assert [u.username for u in first.users] == ["michelle_l", "ihsan_k"]
    assert [u.username for u in second.users] == ["ernest_t"]
    assert first.has_more is True
    assert second.has_more is False


async def test_a_page_reports_more_only_when_there_is_more(
    session: AsyncSession,
) -> None:
    """Exact at the boundary, thanks to the `limit + 1` fetch."""
    await _team(session)

    assert (await user_admin.search_users(session, limit=3)).has_more is False
    assert (await user_admin.search_users(session, limit=2)).has_more is True


async def test_a_registration_mid_read_does_not_shift_the_next_page(
    session: AsyncSession,
) -> None:
    """The reason for keyset: under OFFSET, `ihsan_k` would repeat and
    `ernest_t` vanish."""
    await _team(session)

    first = await user_admin.search_users(session, limit=2)
    await _make(session, "late_arrival", "late@example.com", age=4)

    second = await user_admin.search_users(session, limit=2, cursor=first.next_cursor)

    assert [u.username for u in second.users] == ["ernest_t"]


async def test_the_query_survives_paging(session: AsyncSession) -> None:
    """A cursor carries a position, not a filter; the caller resends both."""
    await _make(session, "ernest_t", "ernest@example.com", age=1)
    await _make(session, "ernest_spare", "spare@example.com", age=2)
    await _make(session, "somebody_else", "else@example.com", age=3)

    first = await user_admin.search_users(session, query="ernest", limit=1)
    second = await user_admin.search_users(
        session, query="ernest", limit=1, cursor=first.next_cursor
    )

    assert [u.username for u in first.users] == ["ernest_spare"]
    assert [u.username for u in second.users] == ["ernest_t"]


async def test_a_cursor_this_service_did_not_issue_is_refused(
    session: AsyncSession,
) -> None:
    """Refused rather than restarting from page one forever."""
    with pytest.raises(MalformedCursor):
        await user_admin.search_users(session, limit=10, cursor="nonsense")


async def test_a_cursor_past_the_end_is_an_empty_page(session: AsyncSession) -> None:
    await _team(session)

    oldest = _EPOCH + timedelta(seconds=1)
    page = await user_admin.search_users(
        session, limit=10, cursor=encode_cursor(oldest, uuid.UUID(int=0))
    )

    assert page.users == []
    assert page.has_more is False


# --- the role and suspension filters [4.1] #479 ----------------------------


async def _mixed_staff(session: AsyncSession) -> None:
    """One account in each of the four role-and-suspension states."""
    await _make(session, "ernest_t", "ernest@example.com", age=1, role=UserRole.ADMIN)
    await _make(
        session,
        "ihsan_k",
        "ihsan@example.com",
        age=2,
        role=UserRole.ADMIN,
        suspended=True,
    )
    await _make(session, "michelle_l", "michelle@example.com", age=3)
    await _make(session, "bob_1", "bob@example.com", age=4, suspended=True)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (UserRole.ADMIN, ["ihsan_k", "ernest_t"]),
        (UserRole.TRADER, ["bob_1", "michelle_l"]),
    ],
)
async def test_a_role_lists_only_the_accounts_holding_it(
    session: AsyncSession, role: UserRole, expected: list[str]
) -> None:
    """"Who are my admins", suspended ones included."""
    await _mixed_staff(session)

    assert await _search(session, role=role) == expected


@pytest.mark.parametrize(
    ("is_suspended", "expected"),
    [
        (True, ["bob_1", "ihsan_k"]),
        (False, ["michelle_l", "ernest_t"]),
    ],
)
async def test_a_suspension_state_lists_only_the_accounts_in_it(
    session: AsyncSession, is_suspended: bool, expected: list[str]
) -> None:
    """"Who is suspended", administrators included."""
    await _mixed_staff(session)

    assert await _search(session, is_suspended=is_suspended) == expected


async def test_the_two_filters_and_the_query_narrow_together(
    session: AsyncSession,
) -> None:
    """Each condition is ANDed: `ihsan_k` alone is an admin, suspended, and
    matched by `example`; `bob_1` misses on the role, `ernest_t` on the
    suspension, and `ihsan_alt` on the query."""
    await _mixed_staff(session)
    await _make(
        session,
        "ihsan_alt",
        "alt@company.co",
        age=5,
        role=UserRole.ADMIN,
        suspended=True,
    )

    assert await _search(
        session, query="example", role=UserRole.ADMIN, is_suspended=True
    ) == ["ihsan_k"]


@pytest.mark.parametrize(
    ("search_filter", "match_columns"),
    [
        ({"role": UserRole.ADMIN}, {"role": UserRole.ADMIN}),
        ({"is_suspended": True}, {"suspended": True}),
    ],
    ids=["role", "is_suspended"],
)
async def test_a_filtered_list_pages_through_every_match_and_nothing_else(
    session: AsyncSession, search_filter: dict, match_columns: dict
) -> None:
    """A non-match between every pair of matches and at both ends, so a filter
    missing from the query, or applied to the fetched rows rather than in it,
    would show a non-match or come up short of the page and misreport
    `has_more`."""
    await _make(session, "other_0", "o0@example.com", age=0)
    await _make(session, "match_1", "m1@example.com", age=1, **match_columns)
    await _make(session, "other_2", "o2@example.com", age=2)
    await _make(session, "match_3", "m3@example.com", age=3, **match_columns)
    await _make(session, "other_4", "o4@example.com", age=4)
    await _make(session, "match_5", "m5@example.com", age=5, **match_columns)
    await _make(session, "other_6", "o6@example.com", age=6)

    first = await user_admin.search_users(session, **search_filter, limit=2)
    second = await user_admin.search_users(
        session, **search_filter, limit=2, cursor=first.next_cursor
    )

    assert [u.username for u in first.users] == ["match_5", "match_3"]
    assert first.has_more is True
    assert [u.username for u in second.users] == ["match_1"]
    assert second.has_more is False


# --- what a search is not -------------------------------------------------


async def test_a_listed_account_does_not_drag_its_sessions_along(
    session: AsyncSession,
) -> None:
    """Refused rather than eager-loaded or emptied: see `_ordered_query`."""
    from sqlalchemy.exc import InvalidRequestError  # noqa: PLC0415

    await _make(session, "ernest_t", "ernest@example.com")

    page = await user_admin.search_users(session, limit=10)

    with pytest.raises(InvalidRequestError):
        _ = page.users[0].refresh_tokens




def test_searching_is_not_an_audited_action() -> None:
    """Boundary guard: no `actor` here, since a search decides nothing. ADR 0006."""
    params = set(inspect.signature(user_admin.search_users).parameters)

    assert params == {
        "session",
        "query",
        "role",
        "is_suspended",
        "limit",
        "cursor",
    }
