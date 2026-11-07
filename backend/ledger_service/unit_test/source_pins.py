"""Reading a sibling service's source, for tests that pin a restated value.

The ledger cannot import market_service (`test_import_boundary.py`), so a
string it restates is pinned by reading the other side's file with `ast`.
`test_market_status.py` and `test_local_copy.py` both pin statuses this way.
"""

from __future__ import annotations

import ast
import pathlib

# `unit_test/` -> `ledger_service/` -> `backend/`.
BACKEND = pathlib.Path(__file__).resolve().parents[2]


def enum_member_of(path: pathlib.Path, enum: str, member: str) -> str:
    """One enum member's string value, read out of a source file with `ast`,
    since market_service cannot be imported (`test_import_boundary.py`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != enum:
            continue
        for stmt in node.body:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else []
            for target in targets:
                if isinstance(target, ast.Name) and target.id == member:
                    value = stmt.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value
                    raise AssertionError(
                        f"{enum}.{member} in {path} is not a plain string literal"
                    )

    raise AssertionError(f"{enum}.{member} not found in {path}")
