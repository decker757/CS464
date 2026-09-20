"""The trader-facing projection. [BE][X] #62. Pure shape, no database.

`unit_test/model/test_schemas.py` does this for the administrator's
`MarketOut`; this is the same job for the two schemas #62 adds. The rules here
are about what the wire carries, so they are asserted directly on the model
rather than only through a route — a route test would prove the same thing
once, slowly, with Postgres running.

Two consumers are being served and they want opposite things from one number:

- **Michelle's create form** needs `liquidity_b` as a JSON number, because it
  compares it against `max_platform_loss` and `"250" + 10` is `"25010"` in a
  browser. `MarketOut` serialises it as a float for that reason and
  `test_the_pricing_numbers_serialise_as_numbers_not_strings` holds it there.
- **The ledger** needs it exact. It reads this endpoint to snapshot a market's
  terms before it can price anything (ADR 0005's publish-time handoff), and
  `b` is the denominator of every price in the system. A float here puts an
  IEEE double at the root of `C(q) = b·ln(Σ e^(q_i/b))`.

Both are right, which is why there are two schemas rather than one changed
one, and why the guard below asserts that `MarketOut` is *still* a float. That
test failing means somebody resolved the conflict by breaking the form.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from model.entities import MarketCard, MarketStatus, displayed_status


def _schemas() -> Any:
    """Imported inside each test rather than at module scope.

    `PublicMarketOut` and `PublicMarketSummaryOut` do not exist yet, so a
    top-level `from model.schemas import PublicMarketOut` would be a collection
    error taking the whole file down as one red line instead of one failure per
    criterion.
    """
    import model.schemas as schemas  # noqa: PLC0415

    return schemas


class _FakeOutcome:
    def __init__(self, position: int, label: str) -> None:
        self.id = uuid.uuid4()
        self.position = position
        self.label = label


class _FakeSource:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.position = 0
        self.url = "https://www.mas.gov.sg/statistics"
        self.label = "MAS official statistics"


class _FakePublishedMarket:
    """A published market as the ORM hands one over.

    Deliberately its own stand-in rather than a reuse of `test_schemas.py`'s
    `_FakeMarket`, which is a DRAFT with no outcomes priced and none of the
    publication columns set. The subject here is a market a trader can see, so
    starting from one that has never been published would make every test
    below assert against a state this projection never receives.

    Naive timestamps, as a driver returns them, so the UTC guard has something
    to do.
    """

    def __init__(
        self,
        *,
        status: MarketStatus = MarketStatus.OPEN,
        closes_in: timedelta = timedelta(days=30),
        liquidity_b: Decimal | None = Decimal("100.0000"),
    ) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)

        self.id = uuid.uuid4()
        self.draft_key = uuid.uuid4()
        self.creator_id = uuid.uuid4()
        self.status = status

        self.question = "Will Singapore core inflation be below 2% in December 2026?"
        self.description = "Core inflation as first published by MAS."
        self.outcomes = [_FakeOutcome(0, "Yes"), _FakeOutcome(1, "No")]

        self.close_time = now + closes_in
        self.resolution_time = now + closes_in + timedelta(days=15)

        self.resolution_criteria = (
            "Resolves YES on the first published MAS print below 2.0%."
        )
        self.resolution_sources = [_FakeSource()]

        self.liquidity_b = liquidity_b
        self.seed_subsidy = Decimal("250.0000")

        self.created_at = now - timedelta(days=2)
        self.updated_at = now - timedelta(days=1)
        self.submitted_at = now - timedelta(days=1)
        self.published_at = now - timedelta(days=1)
        self.closed_at = None

        # A market carrying a proposal, when the status says it has one.
        # PENDING_RESOLUTION and APPROVED are the two states in which these
        # columns are populated on a real row ([3.1] #9, [3.2] #10), and the
        # difference between them is the whole of the gating rule below: one
        # administrator has named a winner, and in only one of the two has a
        # second administrator agreed.
        proposed = status in (MarketStatus.PENDING_RESOLUTION, MarketStatus.APPROVED)
        approved = status is MarketStatus.APPROVED

        self.proposal_id = uuid.uuid4() if proposed else None
        self.proposed_outcome_id = self.outcomes[0].id if proposed else None
        self.proposed_by_id = uuid.uuid4() if proposed else None
        self.proposed_by_username = "ernest_t" if proposed else None
        self.proposed_at = now - timedelta(hours=2) if proposed else None
        self.proposal_evidence_url = (
            "https://www.mas.gov.sg/statistics" if proposed else None
        )
        self.proposal_evidence_note = "First print was 1.8%." if proposed else None

        self.approved_by_id = uuid.uuid4() if approved else None
        self.approved_by_username = "ihsan_b" if approved else None
        self.approved_at = now - timedelta(hours=1) if approved else None

        # What `service/browsing.py::get_published` stamps onto the entity
        # before the detail projection reads it (D-027). Set from the real
        # `displayed_status`, not from a value spelled out here, so these
        # tests still exercise the derivation itself rather than agreeing
        # with a copy of it. That the *service* stamps it is a service-layer
        # fact and is asserted in `test_browsing.py`.
        #
        # An ordinary attribute, exactly as the service sets it — never the
        # mapped `status`, which on a real entity would mark the instance
        # dirty and let a later commit write the derived value to the column.
        self.trader_facing_status = displayed_status(self.status, self.close_time)


def _card(**kwargs: object) -> MarketCard:
    """What `browse` now returns, built the way the service builds it.

    `PublicMarketSummaryOut` is validated from one of these rather than from
    an entity, because that is what the route hands it since D-027 — the
    derivation moved into `service/browsing.py` and the card carries its
    result as a plain `status`. Validating from an entity here would read the
    raw column and silently assert the opposite of the rule.
    """
    entity = _FakePublishedMarket(**kwargs)
    return MarketCard(
        id=entity.id,
        status=displayed_status(entity.status, entity.close_time),
        question=entity.question,
        close_time=entity.close_time,
    )


def _detail_json(**kwargs: object) -> dict[str, Any]:
    model = _schemas().PublicMarketOut.model_validate(_FakePublishedMarket(**kwargs))
    return json.loads(model.model_dump_json())


# --- the ledger's requirement: exact numbers on the wire ------------------
def test_liquidity_b_serialises_as_a_decimal_string_not_a_json_number() -> None:
    """The ledger reads this endpoint to learn `b` before it can price a market.

    `b` is the denominator of every price the platform will ever quote. A JSON
    number is an IEEE double by the time any client has parsed it, so shipping
    it as one puts a rounding error underneath `C(q) = b·ln(Σ e^(q_i/b))` and
    every cost preview and trade computed from it. Exactness is kept in the
    `Numeric(18, 4)` column and it has to survive the wire.

    The same rule, and the same argument, as `ledger-service.md`'s balances.
    """
    payload = _detail_json()

    assert isinstance(payload["liquidity_b"], str), (
        f"liquidity_b is {type(payload['liquidity_b'])}; "
        "a JSON number here is an IEEE double at the root of every price"
    )
    assert Decimal(payload["liquidity_b"]) == Decimal("100")


def test_seed_subsidy_serialises_as_a_decimal_string_too() -> None:
    """The subsidy is money, and it crosses the same boundary.

    ADR 0005 snapshots `b` *and* the subsidy to the trading side at publish,
    and the ledger funds the market pool from it — a movement whose legs have
    to sum to zero against a `Numeric(18, 4)` column. A float subsidy is a
    float credit.
    """
    payload = _detail_json()

    assert isinstance(payload["seed_subsidy"], str)
    assert Decimal(payload["seed_subsidy"]) == Decimal("250")


def test_the_administrators_market_out_still_serialises_them_as_floats() -> None:
    """The guard that makes two schemas the answer rather than one edited one.

    `MarketOut` sends these as numbers on purpose: the create form compares
    `seed_subsidy` against `max_platform_loss` and would otherwise be doing
    arithmetic on strings.
    `test_the_pricing_numbers_serialise_as_numbers_not_strings` in
    `test_schemas.py` is the test that says so, and it is not wrong.

    So this asserts the two coexist. If it fails, the exactness the ledger
    needs was bought by breaking Michelle's form, and the fix is the second
    schema rather than a changed first one.
    """
    from model.schemas import MarketOut  # noqa: PLC0415

    payload = json.loads(
        MarketOut.model_validate(_FakePublishedMarket()).model_dump_json()
    )

    assert isinstance(payload["liquidity_b"], float)
    assert isinstance(payload["seed_subsidy"], float)


def test_every_outcome_carries_its_id_and_position() -> None:
    """The other half of what the ledger needs to open its book.

    `q` is one quantity per outcome, and the realtime contract's `price` frame
    names each one by `outcome_id` and orders them by `position` — so the
    ledger has to snapshot both when it first prices a market, and this is
    where it reads them. `position` is also the order the administrator
    arranged, which is what [X-3] #36 renders.
    """
    payload = _detail_json()

    assert [outcome["position"] for outcome in payload["outcomes"]] == [0, 1]
    for outcome in payload["outcomes"]:
        assert uuid.UUID(outcome["id"])
        assert outcome["label"]


# --- what a trader must not be handed -------------------------------------
@pytest.mark.parametrize("field", ["creator_id", "draft_key"])
def test_the_public_projection_does_not_carry_internal_fields(field: str) -> None:
    """Neither is a trader's business, and neither is harmless.

    `creator_id` names which administrator wrote a market, which invites
    exactly the argument [2.3] #7 and ADR 0016 keep out of the product — an
    outcome is decided by any second administrator, not by whose market it is.
    `draft_key` is the idempotency key of the create form; handing it out lets
    a client address a market by a key the form is still autosaving against.
    """
    assert field not in _detail_json()


# --- ADR 0011: the status a trader is shown is derived --------------------
def test_a_market_within_its_close_time_serialises_as_open() -> None:
    """The ordinary case, stated so the derivation below has a control.

    Without it, a projection that hard-coded `closed` would pass every ADR 0011
    test in this file.
    """
    assert _detail_json()["status"] == MarketStatus.OPEN.value


def test_a_market_past_its_close_time_serialises_as_closed() -> None:
    """ADR 0011: the clock closes a market, and the status column is not the
    authority.

    This market's column says `open` and its `close_time` is a second in the
    past, which is every market for the few seconds between the clock passing
    and the sweeper writing CLOSED. A trader shown `"status": "open"` is shown
    a buy button for a market that has stopped.

    **Stricter than ADR 0011's stated consequence**, which accepts a client
    seeing `"status": "open"` briefly and leaves the frontend to combine the
    status with the close time. Deriving it here is that record's own decision
    applied one reader further, and it is what [X-1] #34's "open markets are
    clearly distinguishable from closed" needs in order to be a property of the
    response. `MarketOut` is unchanged and still reports the column, because an
    administrator does need to know whether the sweep has run.
    """
    payload = _detail_json(closes_in=timedelta(seconds=-1))

    assert payload["status"] == MarketStatus.CLOSED.value


def test_the_administrators_projections_still_report_the_raw_status_column() -> None:
    """The other half of ADR 0011's amendment, and nothing else guards it.

    Same market as the test above — column `open`, `close_time` a second in
    the past — through the two admin projections instead of the public ones.
    Both must still say `open`, because for an administrator that gap *is*
    the information: `close_time` passed and `closed_at` is still null means
    the sweeper has not run, and [2.1] #5's counts are read against the
    column.

    The amendment splits by audience, which only works if both audiences are
    pinned. The public half has three tests above and this had none, so a
    refactor calling `entities.displayed_status` from `_UtcTimestamps` — the
    base both pairs share, and the obvious place to put it — would take the
    administrator's only symptom of a stalled sweep away with the whole suite
    still green.

    The reversal trigger stated in the amendment is a shared reader. If this
    test ever has to change, that is the day it arrived, and the answer is a
    derived field *beside* the column rather than a substitution — not an
    edit to this assertion.
    """
    from model.schemas import MarketOut, MarketSummaryOut  # noqa: PLC0415

    stopped = _FakePublishedMarket(closes_in=timedelta(seconds=-1))

    detail = json.loads(MarketOut.model_validate(stopped).model_dump_json())
    summary = json.loads(MarketSummaryOut.model_validate(stopped).model_dump_json())

    assert detail["status"] == MarketStatus.OPEN.value
    assert summary["status"] == MarketStatus.OPEN.value

    # And the public pair derives on the very same object, so this test fails
    # if the two ever collapse into one answer — in either direction.
    assert _detail_json(closes_in=timedelta(seconds=-1))["status"] == (
        MarketStatus.CLOSED.value
    )


def test_an_early_closed_market_is_not_reopened_by_the_derivation() -> None:
    """[2.3] #7 leaves `close_time` in the future on purpose, and the
    derivation must not undo that.

    An administrator closing a market early does not rewrite `close_time` —
    ADR 0014 keeps the two apart so that a `closed_at` earlier than
    `close_time` is how you tell a hand close from a clock one. So this is the
    one market whose column says CLOSED while its closing time is still days
    away, and a derivation written as "open when `close_time` is in the future"
    puts it back on sale.

    The derivation only ever makes a market *less* tradeable, never more.
    """
    payload = _detail_json(status=MarketStatus.CLOSED, closes_in=timedelta(days=30))

    assert payload["status"] == MarketStatus.CLOSED.value


@pytest.mark.parametrize(
    "status", [MarketStatus.PENDING_RESOLUTION, MarketStatus.APPROVED]
)
def test_a_resolving_status_is_reported_as_it_stands(status: MarketStatus) -> None:
    """[X-3] #36 renders these two differently from a plain closed market, so
    the derivation must not flatten them.

    Both are past trading and both have a `close_time` that may be in either
    direction — a market closed early and then proposed for still has a future
    one. Collapsing them to `closed` would lose the proposal a trader is
    entitled to see under [3.3] #11's dispute window.
    """
    payload = _detail_json(status=status, closes_in=timedelta(days=30))

    assert payload["status"] == status.value


# --- the list projection --------------------------------------------------
def test_a_market_card_carries_the_question_status_and_closing_time() -> None:
    """[X-1] #34: "Each market card shows its question, current YES/NO prices,
    status, and closing time."

    Three of the four. The prices are not this service's to serve — `q` lives
    with the ledger (ADR 0005) and the authoritative read is the snapshot
    endpoint in `docs/api/realtime-service.md`, which lands with [F-3] #43 and
    [T-2] #22. A card renders them from there.
    """
    model = _schemas().PublicMarketSummaryOut.model_validate(_card())
    payload = json.loads(model.model_dump_json())

    assert payload["question"]
    assert payload["status"] == MarketStatus.OPEN.value
    assert payload["close_time"]


def test_the_summary_derives_its_status_the_same_way_the_detail_does() -> None:
    """One rule, two projections, and they must not disagree.

    A browse page that lists a market as open and a detail page that opens
    saying closed is the bug ADR 0011 describes, reproduced inside one service
    by stating the derivation twice.
    """
    stopped = dict(closes_in=timedelta(seconds=-1))

    summary = _schemas().PublicMarketSummaryOut.model_validate(_card(**stopped))
    detail = _schemas().PublicMarketOut.model_validate(
        _FakePublishedMarket(**stopped)
    )

    assert summary.status == detail.status == MarketStatus.CLOSED


# --- timestamps -----------------------------------------------------------
def test_timestamps_carry_an_offset() -> None:
    """The same guard `_UtcTimestamps` gives every other response here.

    A driver hands back naive datetimes, so without it one market serialises
    with a trailing Z and another without, and the frontend special-cases
    which. [X-1] #34 renders a countdown off `close_time`; a missing offset is
    eight hours of countdown in Singapore.
    """
    model = _schemas().PublicMarketOut.model_validate(_FakePublishedMarket())

    assert model.close_time is not None and model.close_time.tzinfo is not None
    assert model.resolution_time is not None
    assert model.resolution_time.tzinfo is not None


# --- a proposed winner is not a decided one -------------------------------
def test_a_pending_proposal_is_not_shown_to_a_trader() -> None:
    """[X-3] #36 asks that *settled* markets display the winning outcome.

    PENDING_RESOLUTION is not settled. It is one administrator's proposal
    with a second administrator yet to rule on it, and ADR 0016 exists
    precisely because that second opinion can go the other way: the reviewer
    rejects, all seven proposal columns are nulled, and the proposer may
    re-propose a different outcome.

    Shipping `proposed_outcome_id` ungated means every trader who loaded the
    page in between saw a winner the platform then reversed — with no
    correction, no notification, and before [3.3] #11's dispute window
    exists to contest it. The field says "winning outcome" to anyone
    rendering it; the status is the only thing that says it is provisional,
    and a client is not obliged to check.
    """
    payload = _detail_json(status=MarketStatus.PENDING_RESOLUTION)

    assert payload["status"] == MarketStatus.PENDING_RESOLUTION.value
    assert payload["proposed_outcome_id"] is None


def test_an_approved_outcome_is_shown() -> None:
    """The other half: once a second administrator has agreed, it is public.

    APPROVED is as far as a market gets today and is the state [X-3] #36's
    criterion is about. Gating must not be so eager that it hides a decided
    winner — that would fail the criterion from the other direction.
    """
    payload = _detail_json(status=MarketStatus.APPROVED)

    assert payload["status"] == MarketStatus.APPROVED.value
    assert payload["proposed_outcome_id"] is not None


@pytest.mark.parametrize(
    "status", [MarketStatus.OPEN, MarketStatus.CLOSED, MarketStatus.PENDING_RESOLUTION]
)
def test_no_status_before_approval_exposes_a_winner(status: MarketStatus) -> None:
    """The rule as a property rather than as one case.

    A market that is still trading should never carry one either — nothing
    stops a proposal's columns surviving on a row whose status moved back,
    and a rejection sends a market to CLOSED with the columns nulled but
    leaves the shape available to a future bug.
    """
    assert _detail_json(status=status)["proposed_outcome_id"] is None
