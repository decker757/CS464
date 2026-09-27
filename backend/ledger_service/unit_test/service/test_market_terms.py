"""The terms pull: the ledger's outbound HTTP call. [F-7] #96, D-008, D-031.

The wire only: the number format, the timeout, and what each upstream failure
becomes. No database; `test_market_books.py` owns what gets written. Driven
through an `httpx.MockTransport`, so the real request and parsing run; the
real market service cannot be imported (`test_import_boundary.py`).
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

def _terms():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import market_terms  # noqa: PLC0415

    return market_terms


def _errors():
    """`core/errors.py`, reached lazily like `_terms`."""
    from core import errors  # noqa: PLC0415

    return errors

# A published market's terms, shaped as `PublicMarketOut` serialises them,
# with the money as JSON strings (D-016), and trimmed to what the ledger reads.
_MARKET_ID = uuid.UUID("410465f3-2852-4833-964b-f42e23b8227c")
_YES = uuid.UUID("4f2a6b1e-0c3d-4a7f-9b12-8e5d6c7a4f30")
_NO = uuid.UUID("9d1c5e84-7b2a-4f13-a6c8-2e0b9d4f7a15")


def _terms_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": str(_MARKET_ID),
        "status": "open",
        "close_time": "2027-01-05T12:00:00Z",
        "resolution_time": "2027-01-20T12:00:00Z",
        "liquidity_b": "100.0000",
        "seed_subsidy": "250.0000",
        "published_at": "2026-09-01T09:00:00Z",
        "outcomes": [
            {"id": str(_YES), "position": 0, "label": "Yes"},
            {"id": str(_NO), "position": 1, "label": "No"},
        ],
    }
    body.update(overrides)
    return body


def _responds(
    status_code: int = 200,
    body: dict[str, object] | None = None,
    *,
    record: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    """A transport that answers every request the same way.

    `record` collects the requests that were made, for the tests that assert on
    the URL and the forwarded token rather than on the answer.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        return httpx.Response(
            status_code,
            json=_terms_body() if body is None else body,
        )

    return httpx.MockTransport(handler)


def _raises(exc: Exception) -> httpx.MockTransport:
    """A transport whose every request fails the way httpx would."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


def _token() -> str:
    from unit_test.conftest import mint_token  # noqa: PLC0415

    return mint_token(uuid.uuid4())


# --- D-016: the number format, which is the whole reason this is a string --
async def test_liquidity_b_arrives_as_the_exact_decimal_that_was_sent() -> None:
    """D-016. Eighteen significant digits fit `Numeric(18, 4)` and not a float,
    so a friendly value would prove nothing.
    """
    exact = Decimal("12345678901234.5678")

    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(liquidity_b=str(exact))),
    )

    assert terms.liquidity_b == exact
    assert isinstance(terms.liquidity_b, Decimal)


async def test_seed_subsidy_arrives_as_the_exact_decimal_too() -> None:
    """The same rule for the money half."""
    exact = Decimal("99999999999999.9999")

    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(seed_subsidy=str(exact))),
    )

    assert terms.seed_subsidy == exact
    assert isinstance(terms.seed_subsidy, Decimal)


async def test_a_json_number_in_the_response_is_still_read_exactly() -> None:
    """Defence in depth: a bare JSON number is still read exactly (D-033)."""
    exact = Decimal("12345678901234.5678")
    raw = json.dumps(_terms_body()).replace('"100.0000"', str(exact))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    terms = await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=httpx.MockTransport(handler)
    )

    assert terms.liquidity_b == exact


async def test_the_outcomes_arrive_with_their_ids_and_positions() -> None:
    """`ledger.market_outcomes` is keyed on these two and stores no label."""
    terms = await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds()
    )

    assert [(o.outcome_id, o.position) for o in terms.outcomes] == [
        (_YES, 0),
        (_NO, 1),
    ]
    assert not any(hasattr(o, "label") for o in terms.outcomes)


async def test_published_at_is_carried_through() -> None:
    """The ledger reads publication from the field, not from the 200."""
    terms = await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds()
    )

    assert terms.published_at == datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


# --- the request this client makes ----------------------------------------
async def test_the_trader_s_token_is_forwarded_as_a_bearer_header() -> None:
    """D-018: a read the ledger makes on a trader's behalf, as a bearer header,
    which market_service prefers over any cookie (ADR 0002).
    """
    token = _token()
    seen: list[httpx.Request] = []

    await _terms().fetch(
        _MARKET_ID, access_token=token, transport=_responds(record=seen)
    )

    assert len(seen) == 1
    assert seen[0].headers["Authorization"] == f"Bearer {token}"


async def test_it_asks_the_public_detail_endpoint_for_that_market() -> None:
    """`GET /public/markets/{id}`, not the creator-scoped admin route."""
    seen: list[httpx.Request] = []

    await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds(record=seen)
    )

    assert seen[0].url.path == f"/public/markets/{_MARKET_ID}"
    assert seen[0].method == "GET"


async def test_the_request_carries_an_explicit_timeout() -> None:
    """D-030. Every phase bounded, asserted on the request's own extensions.

    It cannot catch `timeout=_TIMEOUT` being deleted while the values equal
    httpx's default (D-030); it makes a change to the budget visible.
    """
    seen: list[httpx.Request] = []

    await _terms().fetch(
        _MARKET_ID, access_token=_token(), transport=_responds(record=seen)
    )

    timeout = seen[0].extensions.get("timeout")
    assert timeout is not None, "no timeout was attached to the request"
    assert timeout == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}, (
        f"the request carried a budget nobody recorded a reason for: {timeout}"
    )


# --- D-030: what each kind of failure becomes -----------------------------
async def test_a_connection_error_is_unavailable_not_a_crash() -> None:
    """The market service is down: a retryable 503, not a 500."""
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_raises(httpx.ConnectError("nope")),
        )


async def test_a_timeout_is_unavailable() -> None:
    """Accepted and then silent: the same answer as a refused connection."""
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_raises(httpx.ReadTimeout("slow")),
        )


@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_an_upstream_server_error_is_unavailable(status_code: int) -> None:
    """A 5xx is the market service saying it could not answer."""
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(status_code, body={"detail": "boom"}),
        )


async def test_an_upstream_404_is_not_unavailable() -> None:
    """D-030: a 404 is permanent, so a 503 would tell a trader to retry a
    market that will never appear."""
    with pytest.raises(_errors().MarketNotFound):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(404, body={"code": "market_not_found"}),
        )


async def test_an_upstream_401_propagates_as_not_authenticated() -> None:
    """The forwarded token was rejected, a fact about the caller: log in
    again, not a dependency failure (D-030)."""
    with pytest.raises(_errors().NotAuthenticated):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(401, body={"code": "invalid_token"}),
        )


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"<html>not json</html>", id="a proxy's HTML"),
        # A `ValueError` that is not a `JSONDecodeError`.
        pytest.param(
            b'{"id": ' + b"1" * (sys.get_int_max_str_digits() + 1) + b"}",
            id="an integer longer than Python will parse",
        ),
        # A `RecursionError`, which is not a `ValueError` at all.
        pytest.param(
            b"[" * 100_000 + b"]" * 100_000,
            id="arrays nested deeper than the decoder goes",
        ),
    ],
)
async def test_a_malformed_body_is_unavailable_rather_than_a_crash(
    raw: bytes,
) -> None:
    """A 200 whose bytes do not decode, ADR 0017's "unparseable 200". One case
    per exception `json.loads` raises for it.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw)

    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID, access_token=_token(), transport=httpx.MockTransport(handler)
        )


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("liquidity_b is not a numeral", _terms_body(liquidity_b="abc")),
        ("seed_subsidy is not a numeral", _terms_body(seed_subsidy="")),
        ("id is missing", {k: v for k, v in _terms_body().items() if k != "id"}),
        ("id is not a uuid", _terms_body(id="nope")),
        ("published_at is not a timestamp", _terms_body(published_at="garbage")),
        (
            "an outcome id is not a uuid",
            _terms_body(outcomes=[{"id": "nope", "position": 0}]),
        ),
        ("an outcome has no id", _terms_body(outcomes=[{"position": 0}])),
        ("a position is not a number", _terms_body(outcomes=[{"id": str(_YES), "position": "first"}])),
        # The contract's `position: int`: `int()` truncates one and reads the
        # other as 1.
        ("a position is a fraction", _terms_body(outcomes=[{"id": str(_YES), "position": 1.5}])),
        ("a position is a boolean", _terms_body(outcomes=[{"id": str(_YES), "position": True}])),
        # The field is always present, and only null means unpublished.
        ("published_at is empty", _terms_body(published_at="")),
        ("published_at is false", _terms_body(published_at=False)),
        (
            "published_at is absent",
            {k: v for k, v in _terms_body().items() if k != "published_at"},
        ),
        # `str()` of it is 32 hex digits, which `uuid.UUID` accepts.
        (
            "an outcome id is a JSON number",
            _terms_body(outcomes=[{"id": 12345678901234567890123456789012, "position": 0}]),
        ),
        ("outcomes is not a list of objects", _terms_body(outcomes=["yes", "no"])),
        ("the body is a JSON array", []),
        ("the body is a JSON string", "not a market"),
    ],
)
async def test_valid_json_that_is_not_a_market_is_unavailable(
    label: str, body: object
) -> None:
    """`json.loads` succeeding says the bytes parsed, not that this is a market.

    One case per kind of malformation, because each raises a different
    exception out of the parse.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID, access_token=_token(), transport=_responds(body=body)
        )


async def test_a_true_liquidity_b_is_refused_rather_than_read_as_one() -> None:
    """The malformation that does not raise on its own: `Decimal(True)` is
    `Decimal(1)`, which would open an immutable book at `b = 1`.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(body=_terms_body(liquidity_b=True)),
        )


async def test_a_body_for_a_different_market_is_refused() -> None:
    """A proxy answering with another market's body would otherwise open this
    book with that market's terms, permanently."""
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID,
            access_token=_token(),
            transport=_responds(body=_terms_body(id=str(uuid.uuid4()))),
        )


# --- [F-8] #109: the client carries the status and decides nothing --------
@pytest.mark.parametrize(
    "status", ["closed", "pending_resolution", "approved", "settled"]
)
async def test_fetch_does_not_refuse_a_market_that_is_not_open(
    status: str,
) -> None:
    """ADR 0017: "The client carries the status; the trade path decides on it."
    Settlement, the snapshot and a closed market's book all need these terms.
    """
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(status=status)),
    )

    assert terms.status == status


async def test_a_null_liquidity_b_is_carried_rather_than_refused() -> None:
    """A null `b` is carried; refusing it is `books.ensure_open`'s rule about
    writing a book (ADR 0017)."""
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(liquidity_b=None)),
    )

    assert terms.liquidity_b is None
    assert terms.seed_subsidy == Decimal("250.0000")


async def test_a_null_seed_subsidy_is_carried_rather_than_refused() -> None:
    """The money half of the same rule."""
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(seed_subsidy=None)),
    )

    assert terms.seed_subsidy is None
    assert terms.liquidity_b == Decimal("100.0000")


@pytest.mark.parametrize(
    ("label", "outcomes"),
    [
        ("no outcomes at all", []),
        ("a single outcome", [{"id": str(_YES), "position": 0}]),
        (
            "the same outcome id twice",
            [{"id": str(_YES), "position": 0}, {"id": str(_YES), "position": 1}],
        ),
        (
            "two outcomes claiming one position",
            [{"id": str(_YES), "position": 0}, {"id": str(_NO), "position": 0}],
        ),
    ],
)
async def test_an_unpriceable_outcome_list_is_carried_rather_than_refused(
    label: str, outcomes: list[dict[str, object]]
) -> None:
    """The four unpriceable lists `books.ensure_open` refuses are carried here.
    A malformed individual outcome is still a 503 from the parse.
    """
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(outcomes=outcomes)),
    )

    assert len(terms.outcomes) == len(outcomes)


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("status is absent", {k: v for k, v in _terms_body().items() if k != "status"}),
        ("status is null", _terms_body(status=None)),
        ("status is a number", _terms_body(status=3)),
        ("status is a list", _terms_body(status=["open"])),
        ("status is an object", _terms_body(status={"value": "open"})),
    ],
)
async def test_a_status_that_is_not_a_string_is_unavailable(
    label: str, body: object
) -> None:
    """"A parseable status", ADR 0017. Each case is `!= "open"`, so the gate
    would silently report every market closed; a 503 blames the dependency.
    """
    with pytest.raises(_errors().MarketTermsUnavailable):
        await _terms().fetch(
            _MARKET_ID, access_token=_token(), transport=_responds(body=body)
        )


def test_market_terms_without_a_status_cannot_be_built() -> None:
    """The dataclass holds `_parse`'s line: a default of "open" would make a
    status nobody supplied tradeable on every other construction path.
    """
    with pytest.raises(TypeError):
        _terms().MarketTerms(  # type: ignore[call-arg]
            market_id=_MARKET_ID,
            liquidity_b=None,
            seed_subsidy=None,
            published_at=None,
            outcomes=[],
        )


async def test_a_ten_outcome_market_still_parses() -> None:
    """No count rule survives in `_parse`, in either direction."""
    outcomes = [{"id": str(uuid.uuid4()), "position": i} for i in range(10)]

    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(body=_terms_body(outcomes=outcomes)),
    )

    assert len(terms.outcomes) == 10


async def test_the_close_time_is_not_what_decides_anything_here() -> None:
    """A market past its close time still has terms: ADR 0011 stops trading,
    not reading (ADR 0017)."""
    terms = await _terms().fetch(
        _MARKET_ID,
        access_token=_token(),
        transport=_responds(
            body=_terms_body(
                status="closed",
                close_time=(datetime.now(UTC) - timedelta(days=7)).isoformat(),
            )
        ),
    )

    assert terms.liquidity_b == Decimal("100.0000")
    assert terms.published_at is not None
