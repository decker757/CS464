"""Running Alembic the same way in every service that owns a schema. [F-5] #75

ADR 0020. auth, market and ledger bind this to their own metadata and schema in
`migrations/env.py` and `migrate.py`, and nothing here imports a service. One
copy, because a divergence would be a bug rather than a design choice: a schema
filter that let `audit` in would offer to drop a table no service may touch,
and two adoption rules would adopt a database the third refuses. ADR 0012's bar.

Two things here are load-bearing and not obvious.

Every connection sets `search_path` to `public`, never the service's schema.
When the compared schema is the connection's default, SQLAlchemy reflects its
foreign keys without a schema while the models name one, and compare_metadata
reports every foreign key as dropped and re-added. Checked against Alembic
1.20.0 on all three services' models.

compare_metadata cannot see CHECK constraints, partial-index predicates or
triggers. Each service's guard tests compare those through the catalog, and
`extra_drift` lets a service add a check of its own (the ledger's append-only
trigger) to the adoption of a database from before #75.

Never imported by the realtime or audit services, which have no Alembic.
"""

from __future__ import annotations

from typing import Any


def is_own_name(
    name: str | None, type_: str, parent_names: dict[str, Any], *, schema: str
) -> bool:
    """Alembic's `include_name`: True for `schema` and everything inside it.

    Every other schema is left out, `audit` above all: its one table belongs to
    the superuser. The connection's default schema arrives as None and is
    `public` here, so it is left out too.
    """
    if type_ == "schema":
        return name == schema
    return True


def _describe_change(change: tuple[Any, ...]) -> str:
    kind = change[0]
    if kind in ("add_table", "remove_table"):
        return f"{kind} {change[1].fullname}"
    if kind in ("add_column", "remove_column"):
        _, schema, table, column = change
        return f"{kind} {schema}.{table}.{column.name}"
    if kind.startswith("modify_"):
        _, schema, table, column_name = change[:4]
        return f"{kind} {schema}.{table}.{column_name}"
    # An index, a unique constraint or a foreign key: each knows its table.
    item = change[1]
    return f"{kind} {item.table.fullname}: {item.name or '(unnamed)'}"


def describe_drift(differences: list[Any]) -> list[str]:
    """One line per difference compare_metadata found, naming what differs."""
    lines: list[str] = []
    for difference in differences:
        # Changes to one column arrive grouped in a list.
        if isinstance(difference, list):
            lines.extend(_describe_change(change) for change in difference)
        else:
            lines.append(_describe_change(difference))
    return lines
