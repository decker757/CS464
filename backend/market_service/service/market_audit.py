"""The audit entries a market's lifecycle appends, and what each one carries.
[4.3] #15, [1.3] #3, [2.3] #7, [3.1] #9, [3.2] #10, [3.4] #12.

Every function appends on the caller's session without committing, so the
entry commits with the action it records. ADR 0006.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from model.audit import AdminAction
from model.entities import Market
from model.schemas import PRICING_DECIMAL_PLACES
from service import audit
from service.audit import Actor

# The pricing columns' scale, taken from the request schema so Numeric(18, 4)
# is stated once.
_PRICING_QUANTUM = Decimal(1).scaleb(-PRICING_DECIMAL_PLACES)


def _iso_or_none(value: datetime | None) -> str | None:
    """A timestamp as ISO 8601, or None, written the same way in every audit entry."""
    return value.isoformat() if value is not None else None


def _decimal_or_none(value: Decimal | None) -> str | None:
    """A Decimal as a string at the column's scale, or None.

    Pads, never rounds (the schema refuses extra places), so one value logs as
    "250.0000" whether it came from the request or from Postgres.
    """
    return str(value.quantize(_PRICING_QUANTUM)) if value is not None else None


def _terms_snapshot(market: Market) -> dict[str, object]:
    """The market's terms as an audit `context`, so the log keeps what was approved.

    The keys are the fields `problems_blocking_submission` requires; when one
    grows, so does the other. `description` is prose, not a rule, and stays
    out. ADR 0015.
    """
    return {
        # Strings, not floats: the subsidy is money, and the record should say
        # 250.0000 rather than something that once rounded to it.
        "liquidity_b": _decimal_or_none(market.liquidity_b),
        "seed_subsidy": _decimal_or_none(market.seed_subsidy),
        "outcomes": [outcome.label for outcome in market.outcomes],
        "close_time": _iso_or_none(market.close_time),
        "resolution_time": _iso_or_none(market.resolution_time),
        "resolution_criteria": market.resolution_criteria,
        "resolution_sources": [source.url for source in market.resolution_sources],
    }


def _proposal_snapshot(market: Market) -> dict[str, object]:
    """The proposal as an audit `context`: its id, the winner's id and label, evidence.

    The label is carried because `audit_svc` cannot read the outcomes table.
    The `None` fallback is unreachable (validation refuses a foreign winner)
    and kept on purpose: this runs inside the admin action's transaction, and
    a raising `next()` would abort a valid proposal over a label. ADR 0006.
    """
    winner = next(
        (o for o in market.outcomes if o.id == market.proposed_outcome_id), None
    )
    return {
        # Null, not "None", for a proposal made before ids existed: a
        # reviewer quotes this value back. ADR 0016.
        "proposal_id": (
            str(market.proposal_id) if market.proposal_id is not None else None
        ),
        "winning_outcome_id": str(market.proposed_outcome_id),
        "winning_outcome": winner.label if winner is not None else None,
        "evidence_url": market.proposal_evidence_url,
        "evidence_note": market.proposal_evidence_note,
    }


async def _record(
    session: AsyncSession,
    actor: Actor,
    market: Market,
    action: AdminAction,
    now: datetime,
    *,
    context: dict[str, object],
    reason: str | None = None,
) -> None:
    """Append one audit entry about a market, on the request's session, uncommitted.

    Every market entry names its target the same way. `reason` is only for an
    administrator's justification (an early close, a rejection); a proposal's
    evidence belongs in `context`. ADR 0013, ADR 0014.
    """
    await audit.record(
        session,
        actor=actor,
        action=action,
        target_type="market",
        target_id=market.id,
        target_label=market.question,
        reason=reason,
        context=context,
        now=now,
    )


async def _record_terms(
    session: AsyncSession,
    actor: Actor,
    market: Market,
    action: AdminAction,
    now: datetime,
) -> None:
    """Append an entry carrying the market's terms.

    One builder for submission and publication, so the two entries can be
    compared field for field.
    """
    await _record(session, actor, market, action, now, context=_terms_snapshot(market))


def decision_snapshot(market: Market) -> dict[str, object]:
    """The proposal plus who proposed it and when, for either decision's entry. ADR 0016.

    A rejection must take this before it clears the proposal columns.
    """
    return {
        **_proposal_snapshot(market),
        "proposed_by_id": str(market.proposed_by_id),
        "proposed_by_username": market.proposed_by_username,
        "proposed_at": _iso_or_none(market.proposed_at),
    }


async def record_submission(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append `market.submitted`. An autosave is never logged. [4.3] #15."""
    await _record_terms(session, actor, market, AdminAction.MARKET_SUBMITTED, now)


async def record_publication(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append `market.published`, with its own copy of the terms. [1.3] #3.

    A copy rather than a pointer to the submission entry: a resubmission can
    change the terms, so only this entry says what traders were shown.
    """
    await _record_terms(session, actor, market, AdminAction.MARKET_PUBLISHED, now)


async def record_early_close(
    session: AsyncSession, actor: Actor, market: Market, reason: str, now: datetime
) -> None:
    """Append `market.closed_early`. [2.3] #7, ADR 0014.

    Carries the reason, which is kept nowhere else, and the `close_time` it
    cut short, which `audit_svc` cannot read off the market.
    """
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_CLOSED_EARLY,
        now,
        reason=reason,
        context={"close_time": _iso_or_none(market.close_time)},
    )


async def record_proposal(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append `market.outcome_proposed`. [3.1] #9, ADR 0013.

    Carries the proposal rather than the terms: a rejection clears the
    proposal columns, while a closed market's terms cannot change.
    """
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_OUTCOME_PROPOSED,
        now,
        context=_proposal_snapshot(market),
    )


async def record_approval(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append `market.outcome_approved`, under the approver. [3.2] #10, ADR 0016."""
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_OUTCOME_APPROVED,
        now,
        context=decision_snapshot(market),
    )


async def record_rejection(
    session: AsyncSession,
    actor: Actor,
    market: Market,
    reason: str,
    snapshot: dict[str, object],
    now: datetime,
) -> None:
    """Append `market.outcome_rejected`. [3.2] #10, ADR 0016.

    Takes the snapshot rather than building it, because by now
    `reject_outcome` has cleared the proposal from the market.
    """
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_OUTCOME_REJECTED,
        now,
        reason=reason,
        context=snapshot,
    )


async def record_marked_settled(
    session: AsyncSession, actor: Actor, market: Market, now: datetime
) -> None:
    """Append `market.marked_settled`, under the settling administrator. [3.4] #12.

    Carries the approval's snapshot, so a reader can match the proposal it
    settled on against the ledger's `market.settled` without reading the
    market. ADR 0019's amendment.
    """
    await _record(
        session,
        actor,
        market,
        AdminAction.MARKET_MARKED_SETTLED,
        now,
        context=decision_snapshot(market),
    )
