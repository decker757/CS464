"""Finding every module that writes one entity's table, for the writer
tripwires. [T-3] #23 [T-6] #26

`writers_of` reads the source of every module in this service and names the
ones that construct the entity, hand it to `insert`, `update` or `delete`,
assign to one of its columns, or name its table in a raw write.
"""

from __future__ import annotations

import ast
import pathlib
import re

from core.database import Base

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SKIPPED_DIRECTORIES = {"unit_test", ".venv", "__pycache__"}
_DEFINING_MODULE = "model/entities.py"

_STATEMENT_BUILDERS = {"insert", "update", "delete"}


def _raw_write_of(table: str) -> re.Pattern[str]:
    """A raw `INSERT`, `UPDATE` or `DELETE` naming `table`, schema optional."""
    return re.compile(
        rf"\b(insert\s+into|update|delete\s+from)\s+(\"?ledger\"?\s*\.\s*)?\"?{re.escape(table)}\b",
        re.IGNORECASE,
    )


def _entity_names(tree: ast.Module, entity_name: str) -> set[str]:
    """The local names this module binds to `model.entities.<entity_name>`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "model.entities":
            for alias in node.names:
                if alias.name == entity_name:
                    names.add(alias.asname or alias.name)
    return names


def _module_aliases(tree: ast.Module) -> set[str]:
    """Local names for the `model.entities` module itself, so
    `entities.Position` is recognised as well as a bare `Position`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "model":
            for alias in node.names:
                if alias.name == "entities":
                    names.add(alias.asname or alias.name)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "model.entities":
                    names.add(alias.asname or "model.entities")
    return names


def _is_entity(
    node: ast.expr,
    entity_names: set[str],
    *,
    module_aliases: set[str],
    entity_name: str,
) -> bool:
    """`Position`, `entities.Position` or either one's `.__table__`, for the
    entity named `entity_name`."""
    if isinstance(node, ast.Attribute) and node.attr == "__table__":
        node = node.value
    if isinstance(node, ast.Name):
        return node.id in entity_names
    if isinstance(node, ast.Attribute) and node.attr == entity_name:
        return ast.unparse(node.value) in module_aliases
    return False


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _writes(source: str, entity: type[Base]) -> list[str]:
    """Every place in `source` that writes `entity`, as `line: what`."""
    entity_name = entity.__name__
    table = entity.__tablename__
    columns = {column.key for column in entity.__table__.columns}
    raw_write = _raw_write_of(table)

    tree = ast.parse(source)
    entity_names = _entity_names(tree, entity_name)
    module_aliases = _module_aliases(tree)
    mentions_entity = bool(entity_names) or any(
        _is_entity(
            node,
            entity_names,
            module_aliases=module_aliases,
            entity_name=entity_name,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    )

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_entity(
                node.func,
                entity_names,
                module_aliases=module_aliases,
                entity_name=entity_name,
            ):
                found.append(f"{node.lineno}: constructs a {entity_name}")
            elif (
                _called_name(node) in _STATEMENT_BUILDERS
                and node.args
                and _is_entity(
                    node.args[0],
                    entity_names,
                    module_aliases=module_aliases,
                    entity_name=entity_name,
                )
            ):
                found.append(f"{node.lineno}: {_called_name(node)}({entity_name})")
            elif mentions_entity and _called_name(node) == "setattr":
                found.append(
                    f"{node.lineno}: setattr in a module that uses {entity_name}"
                )

        if mentions_entity and isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in columns:
                    found.append(f"{node.lineno}: assigns .{target.attr}")

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if raw_write.search(node.value):
                found.append(f"{node.lineno}: raw SQL writes {table}")
    return found


def writers_of(entity: type[Base]) -> dict[str, list[str]]:
    """Every module of this service, outside `model/entities.py` and the
    tests, that writes `entity`, with the lines that do it."""
    writers = {}
    for path in sorted(_SERVICE_ROOT.rglob("*.py")):
        relative = path.relative_to(_SERVICE_ROOT)
        if _SKIPPED_DIRECTORIES.intersection(relative.parts):
            continue
        name = relative.as_posix()
        if name == _DEFINING_MODULE:
            continue
        found = _writes(path.read_text(encoding="utf-8"), entity)
        if found:
            writers[name] = found
    return writers
