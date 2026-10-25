"""`is_voidable`, the rule behind the `voidable` flag. [BE] #221, ADR 0025.

"For APPROVED it is `is_in_dispute_window`, the very question send-back
asks, exclusive at its edge." A closed or pending market, and a voided one,
always is; nothing else is. Which statuses feed which argument is the
service suite's (`test_voidable.py` there). No database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.settling import is_voidable

_ENDS_AT = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
_ONE_MICROSECOND = timedelta(microseconds=1)


def _approved(now: datetime, ends_at: datetime | None = _ENDS_AT) -> bool:
    return is_voidable(
        awaiting_outcome=False, approved=True, voided=False, ends_at=ends_at, now=now
    )


def test_an_approved_market_is_voidable_until_its_window_ends_and_not_at_the_end() -> None:
    """ADR 0025's table: APPROVED is voidable while `now < dispute_window_ends_at`,
    and not from it. Fails with `<=` at the edge, or with the window ignored."""
    assert _approved(_ENDS_AT - _ONE_MICROSECOND) is True
    assert _approved(_ENDS_AT) is False


def test_an_approved_market_with_no_window_end_is_not_voidable() -> None:
    """ADR 0025: "APPROVED, window ended, or no `dispute_window_ends_at`: No."
    A year before any end it could have had, so only the null decides."""
    assert _approved(_ENDS_AT - timedelta(days=365), ends_at=None) is False


def test_a_market_awaiting_its_outcome_or_voided_is_voidable_with_no_window() -> None:
    """ADR 0025: CLOSED and PENDING_RESOLUTION are voidable, and VOIDED is
    too, for the direct call. Long past any window and with no end, so each
    fails if put through the window rule."""
    later = _ENDS_AT + timedelta(days=365)

    awaiting = is_voidable(
        awaiting_outcome=True, approved=False, voided=False, ends_at=None, now=later
    )
    voided = is_voidable(
        awaiting_outcome=False, approved=False, voided=True, ends_at=None, now=later
    )

    assert awaiting is True
    assert voided is True


def test_any_other_market_is_not_voidable_even_inside_a_window() -> None:
    """ADR 0025: OPEN, SETTLED, DRAFT and SUBMITTED are not voidable. Given a
    window still open, so it fails if the window rule runs whatever the
    status."""
    voidable = is_voidable(
        awaiting_outcome=False,
        approved=False,
        voided=False,
        ends_at=_ENDS_AT,
        now=_ENDS_AT - timedelta(hours=1),
    )

    assert voidable is False
