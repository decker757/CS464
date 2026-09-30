#!/usr/bin/env bash
# Fails when production code under backend/ has a function, class, method or
# property that nothing uses. [F-11] #198.
#
# Tests are left out of the scan, so a function only its own tests call is
# reported too. Keeping one on purpose is a line in dead_code_allowlist.py.
# vulture matches by name: a call to any function called `close` counts as a
# use of every function called `close`.
#
#   pip install -r requirements-dead-code.txt   # once
#   ./check_dead_code.sh
set -euo pipefail
cd "$(dirname "$0")"

# The decorators register a function with a framework, which then calls it:
# routes, validators, serializers, computed fields and SQLAlchemy event hooks.
framework_decorators="@router.*,@app.*,@field_validator,@model_validator,@field_serializer,@model_serializer,@computed_field,@event.listens_for"

# vulture exits 3 when it finds dead code. Anything else but 0 means it did not
# run (not installed, a bad flag, a file it cannot parse), and must not pass.
status=0
findings=$(python3 -m vulture . dead_code_allowlist.py \
  --exclude "*/unit_test/*,*/.venv/*,shared/testing.py" \
  --ignore-decorators "$framework_decorators" \
  --min-confidence 60 2>&1) || status=$?
if [ "$status" -ne 0 ] && [ "$status" -ne 3 ]; then
  echo "$findings"
  echo "vulture did not run (exit $status). Install it: pip install -r requirements-dead-code.txt" >&2
  exit "$status"
fi

# Unused variables and attributes are left out: nearly all of them are table
# columns and response fields, which SQLAlchemy, pydantic or the frontend read.
dead=$(grep -E "unused (function|class|method|property)" <<< "$findings" || true)

if [ -n "$dead" ]; then
  echo "$dead"
  echo
  echo "Delete it, or add it to backend/dead_code_allowlist.py with the reason it stays."
  exit 1
fi
echo "No dead code."
