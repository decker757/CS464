"""`POST /ledger/markets/{market_id}/void` on the wire. [BE] #221, ADR 0025.

What the controller owns: who may call it, what the body may carry and how
long the reason may be, the shape of the `200` on the first request and on a
repeat, and that every refusal `voiding.void_market` raises reaches the client
in the envelope with the contract's status. The refunds, the stake, the guard
and the races are `unit_test/service/test_void*.py`'s.

The real `void_market` runs under the route, against `VoidUpstream`:
`get_terms_client` is overridden with the production client over its
transport, so both upstream calls are counted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from unit_test.conftest import bearer, terms_client_over
from unit_test.settlement_fixtures import admin, admin_token, committed_counts
from unit_test.trade_fixtures import entities
from unit_test.void_fixtures import REASON, VoidUpstream, open_book, trade

# Every field of a void, and nothing else (the contract's response table).
_FIELDS = {
    "market_id",
    "reason",
    "voided_by",
    "recorded_at",
    "positions_refunded",
    "total_owed",
    "total_refunded",
    "refunds_cut",
    "residue",
}

# The contract's error table, by status.
_CODES = {
    "401": ["invalid_token"],
    "403": ["not_an_administrator", "admin_has_a_stake"],
    "404": ["market_not_found"],
    "409": ["market_already_settled", "market_not_voidable"],
    "422": ["invalid_request", "void_incomplete"],
    "500": ["void_replay_mismatch"],
    "503": ["market_terms_unavailable", "void_unconfirmed"],
}


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/void"


def _assert_refused(response, status: int, code: str) -> None:  # noqa: ANN001
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    assert body["error"]["code"] == code


@pytest.fixture
def upstream() -> VoidUpstream:
    """Open, so a test can trade in it first; `_held` and the tests that trade
    nothing close it, which makes it voidable."""
    return VoidUpstream()


@pytest.fixture
async def void_client(app_under_test, upstream: VoidUpstream):
    """A client whose app reaches `upstream` through the production terms
    client. No Redis override: a void publishes nothing."""
    from controller.dependencies import get_terms_client  # noqa: PLC0415

    async with terms_client_over(upstream.transport) as terms_client:
        app_under_test.dependency_overrides[get_terms_client] = lambda: terms_client
        async with AsyncClient(
            transport=ASGITransport(app=app_under_test), base_url="http://test"
        ) as client:
            yield client


async def _held(session: AsyncSession, upstream: VoidUpstream) -> uuid.UUID:
    """A warm book where one holder bought 12 of outcome 1 at `b = 10`, for
    7.7014 (`test_void.py`'s figure), then closed. The pool holds that plus
    the seed of 2."""
    await open_book(session, upstream)
    holder = uuid.uuid4()
    await trade(session, upstream, holder, outcome=1, side="buy", quantity="12")
    upstream.closes()
    return holder


# =========================================================================
# Who may void
# =========================================================================
async def test_a_trader_may_not_void(
    void_client: AsyncClient, session: AsyncSession, upstream: VoidUpstream
) -> None:
    """ADR 0025, step 1: `403 not_an_administrator`, nothing asked and nothing
    written. The token is valid, the body complete and the market voidable,
    so only the admin gate can refuse: an actor built from any valid token
    would let this trader refund the market."""
    await _held(session, upstream)
    before = await committed_counts(upstream.market_id)

    response = await void_client.post(
        _path(upstream.market_id),
        json={"reason": REASON},
        headers=bearer(uuid.uuid4(), UserRole.TRADER),
    )

    _assert_refused(response, 403, "not_an_administrator")
    assert (upstream.gets, upstream.voids) == (0, 0)
    assert await committed_counts(upstream.market_id) == before


# =========================================================================
# The body: a reason, and nothing else
# =========================================================================
@pytest.mark.parametrize(
    "request_kwargs",
    [
        pytest.param({}, id="no-body"),
        pytest.param({"json": {}}, id="no-reason"),
        pytest.param({"json": {"reason": 1234567890}}, id="a-reason-that-is-not-a-string"),
        pytest.param(
            {"json": {"reason": REASON, "outcome_id": str(uuid.uuid4())}},
            id="a-field-beside-the-reason",
        ),
    ],
)
async def test_a_body_that_is_not_one_reason_is_refused_before_anything_runs(
    void_client: AsyncClient,
    session: AsyncSession,
    upstream: VoidUpstream,
    request_kwargs: dict,
) -> None:
    """The contract's validation row: `reason` missing or not a string, or any
    other field, is `422 invalid_request`, with nothing asked and nothing
    written. Refused, not ignored: the request chooses no outcome, no holder
    and no amount. An admin token on a voidable market with a holder, so
    nothing but the body can refuse."""
    await _held(session, upstream)
    before = await committed_counts(upstream.market_id)

    response = await void_client.post(
        _path(upstream.market_id), headers=bearer(uuid.uuid4(), UserRole.ADMIN), **request_kwargs
    )

    _assert_refused(response, 422, "invalid_request")
    assert (upstream.gets, upstream.voids) == (0, 0)
    assert await committed_counts(upstream.market_id) == before


@pytest.mark.parametrize(
    ("length", "status"), [(5000, 200), (5001, 422)], ids=["5000-accepted", "5001-refused"]
)
async def test_a_reason_may_be_5000_characters_and_no_more(
    void_client: AsyncClient, upstream: VoidUpstream, length: int, status: int
) -> None:
    """ADR 0025, step 1: market_service's ceiling for every reason it stores,
    both edges. Over it is `422 invalid_request`, before market_service is
    asked."""
    upstream.closes()

    response = await void_client.post(
        _path(upstream.market_id),
        json={"reason": "r" * length},
        headers=bearer(uuid.uuid4(), UserRole.ADMIN),
    )

    assert response.status_code == status, response.text
    if status == 422:
        assert response.json()["error"]["code"] == "invalid_request"
        assert upstream.gets == 0


async def test_a_short_reason_is_void_incomplete_with_no_details(
    void_client: AsyncClient, upstream: VoidUpstream
) -> None:
    """The contract: under 10 characters once trimmed is `422
    void_incomplete`, not `invalid_request`, and carries no `error.details`,
    like the administrator's cancel's `cancellation_incomplete`. Five
    characters, so a floor moved into the body's schema would answer
    `invalid_request` here."""
    upstream.closes()

    response = await void_client.post(
        _path(upstream.market_id),
        json={"reason": "short"},
        headers=bearer(uuid.uuid4(), UserRole.ADMIN),
    )

    _assert_refused(response, 422, "void_incomplete")
    assert "details" not in response.json()["error"]
    assert upstream.gets == 0


# =========================================================================
# The response
# =========================================================================
async def test_a_void_is_200_with_the_nine_documented_fields(
    void_client: AsyncClient, session: AsyncSession, upstream: VoidUpstream
) -> None:
    """`200`, exactly the contract's nine fields, with this market's figures:
    one refund of 7.7014 and a residue of the seed, 2.0000. Money crosses as
    a decimal string at scale 4, the count as a JSON integer and the flag as
    a JSON boolean; `recorded_at` carries its offset. The reason is sent
    padded and answered trimmed, as recorded, and `voided_by` is the token's
    `sub`."""
    await _held(session, upstream)
    actor = admin()

    response = await void_client.post(
        _path(upstream.market_id),
        json={"reason": f"  {REASON}  "},
        headers={"Authorization": f"Bearer {admin_token(actor)}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _FIELDS
    assert body["market_id"] == str(upstream.market_id)
    assert body["reason"] == REASON
    assert body["voided_by"] == str(actor.id)
    assert type(body["positions_refunded"]) is int
    assert body["positions_refunded"] == 1
    assert (body["total_owed"], body["total_refunded"], body["residue"]) == (
        "7.7014",
        "7.7014",
        "2.0000",
    )
    assert body["refunds_cut"] is False
    assert datetime.fromisoformat(body["recorded_at"]).utcoffset() is not None


async def test_a_repeat_answers_200_with_a_byte_identical_body(
    void_client: AsyncClient, session: AsyncSession, upstream: VoidUpstream
) -> None:
    """The contract, "A repeat writes nothing here": the same status and the
    same bytes, by another administrator with another reason, and
    market_service told again. A repeat is how `503 void_unconfirmed` is
    finished, so it must be indistinguishable from the first answer. Compared
    raw, because parsing first would hide a formatting difference."""
    await _held(session, upstream)

    first = await void_client.post(
        _path(upstream.market_id),
        json={"reason": REASON},
        headers={"Authorization": f"Bearer {admin_token(admin())}"},
    )
    second = await void_client.post(
        _path(upstream.market_id),
        json={"reason": "Another administrator's reason, never stored."},
        headers={"Authorization": f"Bearer {admin_token(admin(username='ihsan_k'))}"},
    )

    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert first.content == second.content
    assert upstream.voids == 2


# =========================================================================
# Refusals reach the client unchanged
# =========================================================================
async def _unknown(
    session: AsyncSession, upstream: VoidUpstream, actor  # noqa: ANN001
) -> None:
    upstream.get_status_code = 404


async def _open(
    session: AsyncSession, upstream: VoidUpstream, actor  # noqa: ANN001
) -> None:
    await _held(session, upstream)
    upstream.body.update(status="open", voidable=False)


async def _settled(
    session: AsyncSession, upstream: VoidUpstream, actor  # noqa: ANN001
) -> None:
    await _held(session, upstream)
    upstream.body.update(status="settled", voidable=False)


async def _read_unavailable(
    session: AsyncSession, upstream: VoidUpstream, actor  # noqa: ANN001
) -> None:
    await _held(session, upstream)
    upstream.get_status_code = 503


async def _read_unauthorised(
    session: AsyncSession, upstream: VoidUpstream, actor  # noqa: ANN001
) -> None:
    await _held(session, upstream)
    upstream.get_status_code = 401


async def _staked(
    session: AsyncSession, upstream: VoidUpstream, actor  # noqa: ANN001
) -> None:
    await open_book(session, upstream)
    await trade(session, upstream, actor.id, outcome=0, side="buy", quantity="3")
    upstream.closes()


async def _replay_disagrees(
    session: AsyncSession, upstream: VoidUpstream, actor  # noqa: ANN001
) -> None:
    holder = await _held(session, upstream)
    ents = entities()
    await session.execute(
        update(ents.Position)
        .where(ents.Position.user_id == holder, ents.Position.market_id == upstream.market_id)
        .values(quantity=ents.Position.quantity + Decimal("0.0001"))
    )
    await session.commit()


async def _void_step_fails(
    session: AsyncSession, upstream: VoidUpstream, actor  # noqa: ANN001
) -> None:
    await _held(session, upstream)
    upstream.void_status_code = 500


# (setup, status, code, upstream GETs, upstream voids, result rows)
#
# Every case sends an admin token and a valid reason, so 401, 403 and 422 at
# the controller cannot answer. Each row names the step of `void_market` that
# does: the read (unknown, read 5xx, read 401), the status (open, settled),
# the stake, the replay's guard, and the void step, proven by its committed
# result row.
_REFUSALS = [
    pytest.param(_unknown, 404, "market_not_found", 1, 0, 0, id="unknown-market"),
    pytest.param(_open, 409, "market_not_voidable", 1, 0, 0, id="open"),
    pytest.param(_settled, 409, "market_already_settled", 1, 0, 0, id="settled"),
    pytest.param(_read_unavailable, 503, "market_terms_unavailable", 1, 0, 0, id="read-5xx"),
    pytest.param(_read_unauthorised, 401, "invalid_token", 1, 0, 0, id="read-401"),
    pytest.param(_staked, 403, "admin_has_a_stake", 1, 0, 0, id="a-stake"),
    pytest.param(_replay_disagrees, 500, "void_replay_mismatch", 1, 0, 0, id="replay"),
    pytest.param(_void_step_fails, 503, "void_unconfirmed", 1, 1, 1, id="void-step-500"),
]


@pytest.mark.parametrize(("setup", "status", "code", "gets", "voids", "results"), _REFUSALS)
async def test_each_refusal_reaches_the_client_in_the_envelope(
    void_client: AsyncClient,
    session: AsyncSession,
    upstream: VoidUpstream,
    setup,  # noqa: ANN001
    status: int,
    code: str,
    gets: int,
    voids: int,
    results: int,
) -> None:
    """Whatever `void_market` raises, the client gets the contract's status
    and code in the ledger's envelope. A controller that caught or remapped
    any of them, or a status changed in `core/errors.py`, turns its row red:
    `void_unconfirmed` above all, which means the money has moved."""
    actor = admin()
    await setup(session, upstream, actor)
    transactions_before, entries_before, _ = await committed_counts(upstream.market_id)

    response = await void_client.post(
        _path(upstream.market_id),
        json={"reason": REASON},
        headers={"Authorization": f"Bearer {admin_token(actor)}"},
    )

    _assert_refused(response, status, code)
    if status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert (upstream.gets, upstream.voids) == (gets, voids)
    transactions, entries, result_rows = await committed_counts(upstream.market_id)
    assert result_rows == results
    if results == 0:
        assert (transactions, entries) == (transactions_before, entries_before)


# =========================================================================
# The contract at /docs
# =========================================================================
async def test_the_void_declares_the_contracts_responses(void_client: AsyncClient) -> None:
    """`/docs` is the contract with the frontend: the route answers `200`,
    never `201`, and declares every status and code of the contract's error
    table, each code named in its status's description."""
    schema = (await void_client.get("/openapi.json")).json()
    responses = schema["paths"]["/ledger/markets/{market_id}/void"]["post"]["responses"]

    assert set(responses) == {"200", *_CODES}
    for status, codes in _CODES.items():
        for code in codes:
            assert f"`{code}`" in responses[status]["description"], (status, code)
