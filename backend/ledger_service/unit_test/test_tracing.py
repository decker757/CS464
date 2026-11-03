"""What the ledger's SQL spans hold. #599

Top level, like `test_migrations.py`, because it needs the database. The
spec's rule is that no span carries a parameter value: a bound parameter can
hold an idempotency key or an amount, and a trace backend is not where either
belongs. Only the statement text is recorded, with its placeholders.
"""

from __future__ import annotations

from sqlalchemy import literal, select

_SECRET = "param-7f3a-secret"


async def test_a_span_never_holds_a_sql_parameter(session, span_exporter) -> None:
    await session.execute(select(literal(_SECRET)))

    spans = span_exporter.get_finished_spans()
    assert any("SELECT" in span.attributes.get("db.statement", "") for span in spans), (
        "the statement made no SQL span, so this test would pass with nothing traced"
    )
    for span in spans:
        assert all(_SECRET not in str(value) for value in span.attributes.values()), (
            f"span {span.name!r} carries a bound parameter's value"
        )
