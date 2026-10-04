"""The cursor format's field primitives, tested once, where they live. #104

`encode_cursor` and `decode_cursor` are tested through each service's
`core/paging.py`. This covers the N-field pair they are now built on, which the
market browse uses directly with four fields. No database, no settings.
"""

from __future__ import annotations

import base64
import uuid

import pytest

from shared.paging import decode_fields, encode_fields


def test_any_number_of_fields_round_trips() -> None:
    """Four fields, one of them empty, so a decoder that drops or merges empty
    fields fails here."""
    fields = ["2026-10-04T09:15:00.123456+08:00", "1", "", str(uuid.uuid4())]

    assert decode_fields(encode_fields(*fields), count=4) == fields


@pytest.mark.parametrize("count", [3, 5])
def test_a_cursor_with_another_number_of_fields_is_none(count: int) -> None:
    """A two-field audit cursor must never be read as a four-field browse one,
    nor the reverse: the count is part of what a cursor is."""
    assert decode_fields(encode_fields("a", "b", "c", "d"), count=count) is None


@pytest.mark.parametrize(
    "bad",
    [
        "not base64 at all !!",
        base64.urlsafe_b64encode(b"\xff\xfe|x").decode(),
    ],
    ids=["not_base64", "not_utf8"],
)
def test_anything_that_is_not_base64_text_is_none(bad: str) -> None:
    """Both decode failures collapse to None, so the caller raises its own error."""
    assert decode_fields(bad, count=2) is None
