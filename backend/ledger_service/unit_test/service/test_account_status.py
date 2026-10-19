"""The trade path asks auth whether the trader is still active. [4.2] #14, ADR 0022.

`refuse_if_suspended` calls `GET /auth/me` with the trader's own token, once
per trade that is not a replay, after the replay lookup and before the market
gate. The stand-in auth service below is an `httpx.MockTransport` under the
production client, as the terms client's tests do it.

Modules are reached through functions, so a missing name fails the test that
needs it rather than collection (D-007).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import nullcontext

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine
from unit_test.book_fixtures import Stalled
from unit_test.conftest import auth_client_over, idle_in_transaction, mint_token
from unit_test.sell_fixtures import (
    assert_wrote_nothing,
    footprint,
    hold,
    open_at_zero,
    sell,
    version_of,
)
from unit_test.trade_fixtures import (
    QUANTITY,
    Upstream,
    balance_of_user,
    buy,
    entities,
    errors,
    session_factory,
)

_SUSPENDED_BODY = {
    "error": {
        "code": "account_suspended",
        "message": "This account is suspended. Contact an administrator.",
    }
}


def _account_status():
    from service import account_status  # noqa: PLC0415

    return account_status


class _Auth:
    """A stand-in auth service answering `GET /auth/me`, counting calls and
    keeping each request, so a test can say what was asked and how often."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        body: object = None,
        raises: Exception | None = None,
    ) -> None:
        self.calls = 0
        self.requests: list[httpx.Request] = []
        self.status_code = status_code
        self.body = {} if body is None else body
        self.raises = raises

    @classmethod
    def suspended(cls) -> _Auth:
        return cls(status_code=403, body=_SUSPENDED_BODY)

    @property
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            self.requests.append(request)
            if self.raises is not None:
                raise self.raises
            if isinstance(self.body, bytes):
                return httpx.Response(self.status_code, content=self.body)
            return httpx.Response(self.status_code, json=self.body)

        return httpx.MockTransport(handler)


async def _check(session: AsyncSession, auth: _Auth, access_token: str) -> None:
    await _account_status().refuse_if_suspended(
        session, access_token=access_token, auth_client=auth_client_over(auth.transport)
    )


# --- the answer, mapped -----------------------------------------------------
@pytest.mark.parametrize(
    ("make_auth", "refusal"),
    [
        (lambda: _Auth(), None),
        (_Auth.suspended, "AccountSuspended"),
        (
            lambda: _Auth(status_code=401, body={"error": {"code": "invalid_token"}}),
            "NotAuthenticated",
        ),
        (lambda: _Auth(raises=httpx.ConnectError("auth is down")), "AuthUnavailable"),
        (lambda: _Auth(raises=httpx.ReadTimeout("auth is slow")), "AuthUnavailable"),
        (lambda: _Auth(status_code=500), "AuthUnavailable"),
        (
            lambda: _Auth(
                status_code=403, body={"error": {"code": "not_an_administrator"}}
            ),
            "AuthUnavailable",
        ),
        (
            lambda: _Auth(status_code=403, body=b"<html>forbidden</html>"),
            "AuthUnavailable",
        ),
    ],
    ids=[
        "200",
        "403-suspended",
        "401",
        "connect-error",
        "timeout",
        "500",
        "403-another-code",
        "403-not-json",
    ],
)
async def test_auth_s_answer_maps_to_the_trade_error(
    session: AsyncSession, make_auth: Callable[[], _Auth], refusal: str | None
) -> None:
    """ADR 0022's table. Anything but a `200` or a clear answer about the
    token or the account fails closed, as ADR 0017's gate does."""
    auth = make_auth()
    access_token = mint_token(uuid.uuid4())
    expected = (
        nullcontext() if refusal is None else pytest.raises(getattr(errors(), refusal))
    )

    with expected:
        await _check(session, auth, access_token)

    assert auth.calls == 1
    asked = auth.requests[0]
    assert (asked.method, asked.url.path) == ("GET", "/auth/me")
    assert asked.headers["Authorization"] == f"Bearer {access_token}"


# --- on the trade path ------------------------------------------------------
@pytest.mark.parametrize("side", ["buy", "sell"])
async def test_a_suspended_trader_s_trade_is_refused_and_nothing_moves(
    session: AsyncSession, side: str
) -> None:
    """[4.2] #14: suspension blocks trading, and existing positions are kept.

    The trader holds shares bought before the suspension; afterwards neither a
    buy nor a sell writes anything, and the balance and position stay put.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    await hold(session, upstream, user_id=user_id)
    balance = await balance_of_user(session, user_id)
    before = await footprint(session, upstream.market_id)
    auth = _Auth.suspended()

    with pytest.raises(errors().AccountSuspended):
        if side == "buy":
            await buy(
                session,
                upstream,
                user_id=user_id,
                state_version=await version_of(session, upstream.market_id),
                client_key="after-suspension",
                auth_transport=auth.transport,
            )
        else:
            await sell(session, upstream, user_id=user_id, auth_transport=auth.transport)

    await assert_wrote_nothing(session, upstream.market_id, before)
    assert await balance_of_user(session, user_id) == balance


async def test_a_suspended_trader_hears_about_the_suspension_not_the_market(
    session: AsyncSession,
) -> None:
    """ADR 0022's placement: before the market gate, so the refusal is about
    the trader, and market_service is never asked."""
    upstream = Upstream()
    upstream.closes()

    with pytest.raises(errors().AccountSuspended):
        await buy(
            session, upstream, user_id=uuid.uuid4(), auth_transport=_Auth.suspended().transport
        )

    assert upstream.calls == 0


async def test_a_replay_of_a_trade_made_before_suspension_still_answers(
    session: AsyncSession,
) -> None:
    """ADR 0017's order, which ADR 0022 keeps: a replay is answered before any
    check, writes nothing and asks nobody."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    original = await buy(session, upstream, user_id=user_id, client_key="before")
    auth = _Auth.suspended()

    replay = await buy(
        session,
        upstream,
        user_id=user_id,
        quantity=QUANTITY,
        client_key="before",
        state_version=original.state_version,
        auth_transport=auth.transport,
    )

    assert replay == original
    assert auth.calls == 0


async def test_the_suspension_check_holds_no_connection_while_auth_is_slow(
    session: AsyncSession,
) -> None:
    """D-043: a read first, standing in for the trade's replay lookup,
    autobegins a transaction. The check releases it before it calls out, so a
    slow auth holds no pooled connection. Checked from the session, the pool
    and Postgres, at the stalled call. Remove the release and this goes red."""
    auth = _Auth()
    pool = get_engine().pool
    stalled = Stalled(
        auth,
        lambda: {
            "session in a transaction": session.in_transaction(),
            "pooled connections checked out": pool.checkedout(),
        },
    )
    transaction = entities().Transaction
    await session.execute(
        select(transaction.id).where(transaction.idempotency_key == f"trade:{uuid.uuid4()}")
    )
    assert session.in_transaction(), "the read opened no transaction to release"

    task = asyncio.create_task(
        _account_status().refuse_if_suspended(
            session,
            access_token=mint_token(uuid.uuid4()),
            auth_client=auth_client_over(stalled.transport),
        )
    )
    try:
        async with asyncio.timeout(10):
            await stalled.entered.wait()
        idle = await idle_in_transaction()
    finally:
        stalled.release.set()
        await task

    clean = {"session in a transaction": False, "pooled connections checked out": 0}
    assert stalled.seen == [clean]
    assert idle == 0, f"{idle} backend(s) idle in transaction across the auth call"


async def test_a_401_from_auth_is_logged_at_warning(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The ledger has already verified the token itself, so auth refusing the
    same token means the two services disagree about it: a signing key or an
    issuer that differs, or clocks that do. Every trade then fails `401`, and
    without a line in the log nothing says why. ADR 0022's follow-up."""
    auth = _Auth(status_code=401, body={"error": {"code": "invalid_token"}})

    with caplog.at_level("WARNING", logger="service.account_status"):
        with pytest.raises(errors().NotAuthenticated):
            await _check(session, auth, mint_token(uuid.uuid4()))

    logged = [r.levelname for r in caplog.records if r.name == "service.account_status"]
    assert logged == ["WARNING"]


async def test_a_suspended_traders_attempt_on_a_cold_market_opens_no_book_and_mints_nothing(
    session: AsyncSession,
) -> None:
    """ADR 0022: before `books.ensure_open` and `grants.ensure_granted`, "so a
    suspended account's attempt cannot open and fund a cold market's pool or
    mint its own starting grant". The market has never been touched, and the
    trader never read a balance."""
    upstream = Upstream()
    user_id = uuid.uuid4()

    with pytest.raises(errors().AccountSuspended):
        await buy(
            session, upstream, user_id=user_id, auth_transport=_Auth.suspended().transport
        )

    ents = entities()
    async with session_factory()() as other:
        for table in (ents.MarketBook, ents.Account, ents.Transaction):
            count = (await other.execute(select(func.count()).select_from(table))).scalar_one()
            assert count == 0, f"a suspended attempt wrote to {table.__tablename__}"
    assert upstream.calls == 0
