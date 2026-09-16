"""Finding an account to investigate. [4.1] #13

The first of the story's three acceptance criteria, asserted against the
service layer with no HTTP in the way. The other two are the ledger's: this
service does not know that credits exist, and the search exists to turn a name
somebody typed into the `user_id` the ledger's history routes take.

Users are inserted directly rather than registered, because these tests are
about reading and every one of them needs several accounts at timestamps it
controls. `password_hash` is a placeholder for the same reason the
`another_administrator` fixture uses one: nothing here logs in.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MalformedCursor
from core.paging import encode_cursor
from model.entities import User
from service import user_admin

# Fixed rather than `now`, so the order these tests assert on is the order they
# are written in and not a race against the clock.
_EPOCH = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


async def _make(
    session: AsyncSession,
    username: str,
    email: str,
    *,
    age: int = 0,
    suspended: bool = False,
) -> User:
    """One account, registered `age` seconds after the epoch above.

    Older `age` means older account, so the list under test comes back in
    descending `age` order.
    """
    user = User(
        username=username,
        email=email,
        password_hash="not-a-real-hash",
        is_suspended=suspended,
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
    """One box for both. An administrator holding one of the two should not
    have to tell the server which it is."""
    await _team(session)

    assert await _search(session, query="company.co") == ["ihsan_k"]


async def test_the_match_is_case_insensitive(session: AsyncSession) -> None:
    """Names are stored as they were typed and the person searching did not
    see them being typed."""
    await _make(session, "Ernest_T", "Ernest@Example.com")

    assert await _search(session, query="ERNEST") == ["Ernest_T"]
    assert await _search(session, query="ernest") == ["Ernest_T"]


async def test_a_fragment_matching_nobody_finds_nobody(session: AsyncSession) -> None:
    await _team(session)

    assert await _search(session, query="nobody-by-that-name") == []


async def test_an_absent_query_lists_everybody(session: AsyncSession) -> None:
    """So the page opens on the user list rather than an empty state waiting to
    be typed into."""
    await _team(session)

    assert len(await _search(session)) == 3


async def test_a_blank_query_lists_everybody(session: AsyncSession) -> None:
    """A cleared search box sends `?q=`, and an emptied box means "show me
    everybody again", not "show me nobody"."""
    await _team(session)

    assert len(await _search(session, query="   ")) == 3


# --- the query is text, not a pattern -------------------------------------


async def test_an_underscore_is_matched_literally(session: AsyncSession) -> None:
    """The reason the escaping exists at all.

    `_` is LIKE's single-character wildcard and it is also legal in every
    username this service accepts — most of the team has one. Unescaped, a
    search for `ernest_t` quietly returns `ernestXt` as well, which is a wrong
    account handed to somebody investigating an anomaly.
    """
    await _make(session, "ernest_t", "ernest@example.com", age=1)
    await _make(session, "ernestXt", "other@example.com", age=2)

    assert await _search(session, query="ernest_t") == ["ernest_t"]


async def test_a_percent_is_matched_literally(session: AsyncSession) -> None:
    """The other wildcard. `%` cannot appear in a username but can in the local
    part of an address, and either way a search for it must not match the whole
    table."""
    await _team(session)

    assert await _search(session, query="%") == []


async def test_a_backslash_is_matched_literally(session: AsyncSession) -> None:
    """The escape character itself, which has to be escaped first or it would
    escape the escapes. Nobody here has one in their name; the assertion is
    that this is a search that finds nothing rather than a 500."""
    await _team(session)

    assert await _search(session, query="\\") == []


# --- ordering and paging --------------------------------------------------


async def test_the_newest_registration_comes_first(session: AsyncSession) -> None:
    await _team(session)

    assert await _search(session) == ["michelle_l", "ihsan_k", "ernest_t"]


async def test_a_suspended_account_is_still_listed(session: AsyncSession) -> None:
    """The opposite of `_administrators_for_update`, which excludes them
    deliberately. A suspended account is exactly the one somebody investigating
    an anomaly is looking for, and hiding it would make the list lie about who
    exists."""
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
    """`limit + 1` fetched and the extra dropped, so `has_more` is exact rather
    than a guess from a count that would be stale by the time it rendered."""
    await _team(session)

    assert (await user_admin.search_users(session, limit=3)).has_more is False
    assert (await user_admin.search_users(session, limit=2)).has_more is True


async def test_a_registration_mid_read_does_not_shift_the_next_page(
    session: AsyncSession,
) -> None:
    """The whole reason this is keyset and not OFFSET.

    With `OFFSET 2`, an account registered between the two calls pushes the
    window down and `ihsan_k` comes back a second time while `ernest_t` is
    never seen. A cursor names a position, so the new account is simply newer
    than it.
    """
    await _team(session)

    first = await user_admin.search_users(session, limit=2)
    await _make(session, "late_arrival", "late@example.com", age=4)

    second = await user_admin.search_users(session, limit=2, cursor=first.next_cursor)

    assert [u.username for u in second.users] == ["ernest_t"]


async def test_the_query_survives_paging(session: AsyncSession) -> None:
    """A cursor carries a position, not a filter, so the caller resends both.
    A page 2 that quietly dropped the search would be a page of strangers."""
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
    """Refused rather than ignored: silently restarting from the newest page
    would leave a client looping over page 1 forever."""
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


# --- what a search is not -------------------------------------------------


async def test_a_listed_account_does_not_drag_its_sessions_along(
    session: AsyncSession,
) -> None:
    """`User.refresh_tokens` is `lazy="selectin"`, so the default for this
    query is a second one fetching every live session of every account on the
    page — unbounded rows, for data no response contains. Refused rather than
    emptied, so that a caller who does want them is told to ask."""
    from sqlalchemy.exc import InvalidRequestError  # noqa: PLC0415

    await _make(session, "ernest_t", "ernest@example.com")

    page = await user_admin.search_users(session, limit=10)

    with pytest.raises(InvalidRequestError):
        _ = page.users[0].refresh_tokens




def test_searching_is_not_an_audited_action() -> None:
    """Boundary guard for ADR 0006.

    The log records decisions, not keystrokes. An `actor` on this signature
    would mean somebody had given a read an actor to attribute, which is the
    first half of writing an entry per search — and one administrator working
    through a list would then bury every promotion and suspension in it.
    """
    params = set(inspect.signature(user_admin.search_users).parameters)

    assert params == {"session", "query", "limit", "cursor"}
