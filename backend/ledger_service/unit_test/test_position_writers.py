"""Only the trade path writes `ledger.positions`. [T-3] #23

**A tripwire, not a test of behaviour.** Nothing here runs a trade. It reads
the source of every module in this service and fails when a module other
than `service/trading.py` writes a `Position` — constructs one, hands the
entity to `insert`, `update` or `delete`, assigns to one of its columns, or
names the table in a raw `INSERT`, `UPDATE` or `DELETE`.

It exists because of the D-NEW entry "The holdings check is read under the
book lock, and the position takes no lock of its own". The sell's holding
check reads the position with no `with_for_update()`, and that is safe only
because every writer of positions takes the market's book row lock first.
The trade path does. A second writer — settlement, [3.4] #12, is the
candidate PR #120's review named — that skipped the book lock would read the
same share count as a concurrent sell, and one would overwrite the other.

When this fails, do not just add the module to `_KNOWN_WRITERS`. Read that
entry first. Either the new writer takes the book lock before it reads or
writes a position, and says so beside its line below, or the entry's
reversal trigger has fired and the position read gets its own `FOR UPDATE`,
in both writers.

The scan is deliberately coarse: a module that imports the `Position` entity
and assigns to any attribute named like one of its columns counts as a
writer. A false alarm costs one look; a missed writer costs a lost update.
"""

from __future__ import annotations

import ast
import pathlib
import re

from model.entities import Position

# Every module allowed to write a position, and how it holds the row still.
_KNOWN_WRITERS = {
    "service/trading.py": "takes the book row's FOR UPDATE before it reads a position",
}

_SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SKIPPED_DIRECTORIES = {"unit_test", ".venv", "__pycache__"}
_DEFINING_MODULE = "model/entities.py"

_COLUMNS = {column.key for column in Position.__table__.columns}
_STATEMENT_BUILDERS = {"insert", "update", "delete"}
_RAW_WRITE = re.compile(
    r"\b(insert\s+into|update|delete\s+from)\s+(\"?ledger\"?\s*\.\s*)?\"?positions\b",
    re.IGNORECASE,
)


def _entity_names(tree: ast.Module) -> set[str]:
    """The local names this module binds to `model.entities.Position`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "model.entities":
            for alias in node.names:
                if alias.name == "Position":
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


def _is_entity(node: ast.expr, entity_names: set[str], module_aliases: set[str]) -> bool:
    """`Position`, `entities.Position` or either one's `.__table__`."""
    if isinstance(node, ast.Attribute) and node.attr == "__table__":
        node = node.value
    if isinstance(node, ast.Name):
        return node.id in entity_names
    if isinstance(node, ast.Attribute) and node.attr == "Position":
        return ast.unparse(node.value) in module_aliases
    return False


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _writes(source: str) -> list[str]:
    """Every place in `source` that writes a position, as `line: what`."""
    tree = ast.parse(source)
    entity_names = _entity_names(tree)
    module_aliases = _module_aliases(tree)
    mentions_entity = bool(entity_names) or any(
        _is_entity(node, entity_names, module_aliases)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    )

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_entity(node.func, entity_names, module_aliases):
                found.append(f"{node.lineno}: constructs a Position")
            elif (
                _called_name(node) in _STATEMENT_BUILDERS
                and node.args
                and _is_entity(node.args[0], entity_names, module_aliases)
            ):
                found.append(f"{node.lineno}: {_called_name(node)}(Position)")
            elif mentions_entity and _called_name(node) == "setattr":
                found.append(f"{node.lineno}: setattr in a module that uses Position")

        if mentions_entity and isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in _COLUMNS:
                    found.append(f"{node.lineno}: assigns .{target.attr}")

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _RAW_WRITE.search(node.value):
                found.append(f"{node.lineno}: raw SQL writes positions")
    return found


def _position_writers() -> dict[str, list[str]]:
    writers = {}
    for path in sorted(_SERVICE_ROOT.rglob("*.py")):
        relative = path.relative_to(_SERVICE_ROOT)
        if _SKIPPED_DIRECTORIES.intersection(relative.parts):
            continue
        name = relative.as_posix()
        if name == _DEFINING_MODULE:
            continue
        found = _writes(path.read_text(encoding="utf-8"))
        if found:
            writers[name] = found
    return writers


def test_the_trade_path_is_the_only_writer_of_positions() -> None:
    """Also fails if the scan stops seeing `service/trading.py`, so a scan
    that has gone blind cannot pass by finding nothing anywhere."""
    writers = _position_writers()

    assert set(writers) == set(_KNOWN_WRITERS), (
        "the modules that write ledger.positions changed. A new writer must "
        "take the book row lock before it touches a position, or the position "
        "read gets its own FOR UPDATE — see the D-NEW entry \"The holdings "
        "check is read under the book lock, and the position takes no lock of "
        "its own\".\n"
        + "\n".join(f"{name}: {found}" for name, found in writers.items())
    )
