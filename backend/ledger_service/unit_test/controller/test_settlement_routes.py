"""Status codes, authorisation and the shape on the wire. [3.4] #12

Business rules are tested in `unit_test/service/test_settlement*.py` without
HTTP. What is asserted here is what the controller owns: who may call the
route, what the body may contain, the status of a fresh settlement and of a
repeat, the token forwarded to market_service, and that every refusal
`pay_out` raises reaches the client in the envelope unchanged.

The real `pay_out` runs under the route, against a stand-in market_service:
`get_terms_client` is overridden with the production client over
`SettleUpstream.transport`, so both upstream calls are counted.

**Not tested here: "any administrator may settle, including the market's
creator, its proposer and its approver".** It holds by construction: the
public detail the ledger reads carries no creator, proposer or approver id,
so the ledger cannot tell those administrators apart from any other. A test
would need a stub field the real wire does not carry, which tests a fake
contract. It becomes testable when the wire changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from unit_test.conftest import bearer, terms_client_over
from unit_test.settlement_fixtures import (
    WINNER,
    SettleUpstream,
    admin,
    admin_token,
    committed_counts,
    hold,
    warm,
)

# Every field in a settlement, and nothing else. A set, so that a field the
# fresh path grows and a replay does not is a failure here.
_FIELDS = {
    "market_id",
    "outcome_id",
    "recorded_at",
    "holders_paid",
    "total_paid",
    "residue",
}

_HELD = Decimal("10.0000")


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/settlement"


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token(admin())}"}


def _assert_refused(response, status: int, code: str) -> None:  # noqa: ANN001
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    assert body["error"]["code"] == code


@pytest.fixture
def upstream() -> SettleUpstream:
    """Approved, settleable, `WINNER` proposed, unless a test changes it."""
    return SettleUpstream()


@pytest.fixture
async def settle_client(app_under_test, upstream: SettleUpstream):
    """A client whose app reaches `upstream` through the production terms
    client. No Redis override: settlement publishes nothing, so the route
    must not depend on it."""
    from controller.dependencies import get_terms_client  # noqa: PLC0415

    async with terms_client_over(upstream.transport) as terms_client:
        app_under_test.dependency_overrides[get_terms_client] = lambda: terms_client
        async with AsyncClient(
            transport=ASGITransport(app=app_under_test), base_url="http://test"
        ) as client:
            yield client


# =========================================================================
# Who may settle
# =========================================================================
async def test_a_settlement_needs_a_token(
    settle_client: AsyncClient, session: AsyncSession, upstream: SettleUpstream
) -> None:
    """401 with the challenge header, and market_service is never asked. The
    book is warm and the market settleable, so without the guard this would
    pay out."""
    await warm(session, upstream)

    response = await settle_client.post(_path(upstream.market_id))

    _assert_refused(response, 401, "invalid_token")
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert (upstream.gets, upstream.posts) == (0, 0)


async def test_a_trader_may_not_settle(
    settle_client: AsyncClient, session: AsyncSession, upstream: SettleUpstream
) -> None:
    """403 `not_an_administrator`, nothing written, nothing asked. The token
    is valid and there is no body, so only the admin gate can refuse: an
    actor built on `CurrentUser` would let this trader pay the market out."""
    await warm(session, upstream)
    before = await committed_counts(upstream.market_id)

    response = await settle_client.post(
        _path(upstream.market_id), headers=bearer(uuid.uuid4(), UserRole.TRADER)
    )

    _assert_refused(response, 403, "not_an_administrator")
    assert (upstream.gets, upstream.posts) == (0, 0)
    assert await committed_counts(upstream.market_id) == before


# =========================================================================
# The body: empty, and nothing else
# =========================================================================
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome_id", str(uuid.uuid4())),
        ("account_id", str(uuid.uuid4())),
        ("amount", "1000.0000"),
    ],
    ids=["outcome_id", "account_id", "amount"],
)
async def test_a_body_naming_anything_is_refused_before_anything_runs(
    settle_client: AsyncClient,
    session: AsyncSession,
    upstream: SettleUpstream,
    field: str,
    value: str,
) -> None:
    """422 `invalid_request` naming the field, nothing written, nothing
    asked. Refused, not ignored: a dropped `outcome_id` would let a client
    believe it chose the winner (ADR 0009's second amendment).

    An admin token, so neither 401 nor 403 can answer first. The book is warm
    and the market settleable, so with `extra="forbid"` removed, or with no
    body parameter at all, this request would settle and answer 200.
    """
    await warm(session, upstream)
    before = await committed_counts(upstream.market_id)

    response = await settle_client.post(
        _path(upstream.market_id), json={field: value}, headers=_admin_headers()
    )

    _assert_refused(response, 422, "invalid_request")
    assert ["body", field] in [item["loc"] for item in response.json()["error"]["details"]]
    assert (upstream.gets, upstream.posts) == (0, 0)
    assert await committed_counts(upstream.market_id) == before


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {},
        {"json": {}},
        {"content": b"null", "headers": {"Content-Type": "application/json"}},
    ],
    ids=["no-body", "empty-object", "null"],
)
async def test_no_body_an_empty_object_and_null_are_accepted(
    settle_client: AsyncClient,
    session: AsyncSession,
    upstream: SettleUpstream,
    request_kwargs: dict,
) -> None:
    """The documented call is a bare POST. A required body would refuse the
    bare POST and `null` with "field required"; `{}` pins the documented
    promise that what most JSON clients send for "nothing" is accepted."""
    await warm(session, upstream)
    headers = {**_admin_headers(), **request_kwargs.pop("headers", {})}

    response = await settle_client.post(
        _path(upstream.market_id), headers=headers, **request_kwargs
    )

    assert response.status_code == 200, response.text


# =========================================================================
# The response
# =========================================================================
async def test_a_settlement_is_200_with_the_six_documented_fields(
    settle_client: AsyncClient, session: AsyncSession, upstream: SettleUpstream
) -> None:
    """200, exactly six fields, with the figures this book implies: one
    winner holding 10 shares of a pool seeded with 250.

    Catches a field added or dropped, a wrong figure, money typed as a JSON
    number or `holders_paid` as anything but a JSON integer, and a
    `recorded_at` leaving without an offset. Money crosses as a decimal
    string at scale 4; a count is a number.
    """
    await warm(session, upstream)
    await hold(session, upstream, [(uuid.uuid4(), WINNER, _HELD)])

    response = await settle_client.post(
        _path(upstream.market_id), headers=_admin_headers()
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _FIELDS
    assert body["market_id"] == str(upstream.market_id)
    assert body["outcome_id"] == str(upstream.winner)
    assert type(body["holders_paid"]) is int
    assert body["holders_paid"] == 1
    assert body["total_paid"] == "10.0000"
    assert body["residue"] == "240.0000"
    assert datetime.fromisoformat(body["recorded_at"]).utcoffset() is not None


async def test_a_repeat_answers_200_with_a_byte_identical_body(
    settle_client: AsyncClient, session: AsyncSession, upstream: SettleUpstream
) -> None:
    """The same status and the same bytes, and market_service told again.

    A repeat is how a failed last step is repaired, so it must be
    indistinguishable from the first answer: `201` on the fresh path only
    would split the statuses, and any field built outside the one builder
    (a `recorded_at` stamped in the controller, say) would split the bytes.
    Compared raw, because parsing first would hide a formatting difference.
    """
    await warm(session, upstream)
    await hold(session, upstream, [(uuid.uuid4(), WINNER, _HELD)])
    headers = _admin_headers()

    first = await settle_client.post(_path(upstream.market_id), headers=headers)
    second = await settle_client.post(_path(upstream.market_id), headers=headers)

    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert first.content == second.content
    assert upstream.posts == 2
    assert upstream.gets == 2


@pytest.mark.parametrize("carrier", ["header", "cookie"])
async def test_the_administrators_own_token_reaches_the_settle_step(
    settle_client: AsyncClient,
    session: AsyncSession,
    upstream: SettleUpstream,
    carrier: str,
) -> None:
    """The token the administrator sent, and no other, is what
    market_service's settle step sees, whether it arrived as a bearer header
    or as the browser's cookie (D-018, ADR 0019). A minted service token, or
    none, would not match."""
    await warm(session, upstream)
    token = admin_token(admin())
    if carrier == "header":
        response = await settle_client.post(
            _path(upstream.market_id), headers={"Authorization": f"Bearer {token}"}
        )
    else:
        settle_client.cookies.set("access_token", token)
        response = await settle_client.post(_path(upstream.market_id))

    assert response.status_code == 200, response.text
    assert upstream.post_tokens == [token]


# =========================================================================
# Refusals reach the client unchanged
# =========================================================================
def _unknown(upstream: SettleUpstream) -> None:
    upstream.get_status_code = 404


def _closed(upstream: SettleUpstream) -> None:
    upstream.body["status"] = "closed"


def _inside_the_window(upstream: SettleUpstream) -> None:
    upstream.body["settleable"] = False


def _read_unavailable(upstream: SettleUpstream) -> None:
    upstream.get_status_code = 503


def _read_unauthorised(upstream: SettleUpstream) -> None:
    upstream.get_status_code = 401


def _settle_step_fails(upstream: SettleUpstream) -> None:
    upstream.post_status_code = 500


# (setup, warm book, status, code, upstream GETs, upstream POSTs, result rows)
#
# Every case sends an admin token and no body, so 401, 403 and 422 at the
# controller cannot answer. Each row says which step of `pay_out` does:
# - unknown: the read, with no book, since a 404 for a market with a book is
#   503 (ADR 0017);
# - closed, inside the window: the status check, as the read succeeds and a
#   fresh database holds no record;
# - read 503, read 401: the read, proven by one GET; nothing written rules
#   out the later 503s;
# - settle step: the last step, proven by the committed result row.
_REFUSALS = [
    pytest.param(_unknown, False, 404, "market_not_found", 1, 0, 0, id="unknown-market"),
    pytest.param(_closed, True, 409, "market_not_approved", 1, 0, 0, id="closed"),
    pytest.param(
        _inside_the_window, True, 409, "dispute_window_open", 1, 0, 0, id="dispute-window"
    ),
    pytest.param(
        _read_unavailable, True, 503, "market_terms_unavailable", 1, 0, 0, id="read-5xx"
    ),
    pytest.param(_read_unauthorised, True, 401, "invalid_token", 1, 0, 0, id="read-401"),
    pytest.param(
        _settle_step_fails, True, 503, "settlement_unconfirmed", 1, 1, 1, id="settle-step-500"
    ),
]


@pytest.mark.parametrize(
    ("setup", "warm_book", "status", "code", "gets", "posts", "results"), _REFUSALS
)
async def test_each_refusal_reaches_the_client_in_the_envelope(
    settle_client: AsyncClient,
    session: AsyncSession,
    upstream: SettleUpstream,
    setup,  # noqa: ANN001
    warm_book: bool,
    status: int,
    code: str,
    gets: int,
    posts: int,
    results: int,
) -> None:
    """Whatever `pay_out` raises, the client gets that status and code in the
    ledger's envelope. A controller that caught or remapped any of them, such
    as turning `settlement_unconfirmed` into a failure the administrator
    retries differently, turns its row red."""
    if warm_book:
        await warm(session, upstream)
    setup(upstream)
    transactions_before, entries_before, _ = await committed_counts(upstream.market_id)

    response = await settle_client.post(
        _path(upstream.market_id), headers=_admin_headers()
    )

    _assert_refused(response, status, code)
    if status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert (upstream.gets, upstream.posts) == (gets, posts)
    transactions, entries, result_rows = await committed_counts(upstream.market_id)
    assert result_rows == results
    if results == 0:
        assert (transactions, entries) == (transactions_before, entries_before)


# =========================================================================
# The contract at /docs
# =========================================================================
async def test_the_route_is_documented_as_a_post_that_answers_200(
    settle_client: AsyncClient,
) -> None:
    """`/docs` is the contract with the frontend. The settlement answers 200
    on a fresh settlement and on a repeat alike, and never 201: nothing is
    created at an address (DECISIONS.md, "The settlement route answers
    `200`")."""
    schema = (await settle_client.get("/openapi.json")).json()
    path = schema["paths"]["/ledger/markets/{market_id}/settlement"]

    assert "post" in path
    assert "200" in path["post"]["responses"]
    assert "201" not in path["post"]["responses"]
