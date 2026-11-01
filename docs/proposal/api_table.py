"""Generate the proposal's API endpoint table (§2.2) from the services themselves.

Nothing in the tables is typed by hand. Each service's FastAPI `app` is
imported from `backend/<service>/main.py`, `app.openapi()` gives the routes,
and what OpenAPI cannot say is read from the same `app` mechanically, as
described below.

How to run
----------
From the repository root, with any Python 3.11+ (only the standard library is
needed here; each service is imported with its own virtualenv):

    python docs/proposal/api_table.py --venvs <dir holding <service>/.venv>

`--venvs` defaults to this checkout's `backend/`. A git worktree has no
virtualenvs, so point it at the main checkout's `backend/`. The audit service
has no virtualenv of its own and is imported with the market service's, which
pins the same libraries (`VENV_FALLBACKS`).

The script runs itself once per service as a subprocess (`--collect
<service>`) under that service's virtualenv, with `PYTHONPATH` set to the
service folder and `backend/`, the settings each service requires set to
placeholders in that process's environment only (`PLACEHOLDER_ENV`), and a
fresh empty working directory so no `.env` file is read. Importing `main` builds
the app and connects to nothing: engines, Redis and HTTP clients are created
lazily or in the lifespan, which never runs here. Each subprocess prints JSON;
this process renders the markdown and writes:

    docs/proposal/api-endpoints.md       every route, grouped by service
    docs/proposal/api-endpoints-body.md  the routes named in BODY_ROUTES, with
                                         every status and its first codes
    docs/proposal/api-endpoints-appendix.md  every route with error statuses
                                         only, to fit one page

How each column is derived
--------------------------
Method, Path, Service
    `app.openapi()["paths"]`. Every operation is matched to its route object
    by `operationId == route.unique_id`. The price socket is not in OpenAPI,
    so WebSocket routes are taken from `app.routes` (method shown as `WS`).
    Operations tagged `ops` (`GET /health`, one per service) are left out of
    the rows and named once in the conventions line.

Reachable code (used by the next three columns)
    Starting from the route's endpoint and every callable in its `dependant`
    tree (FastAPI's resolved `Depends(...)` graph), the script parses each
    function's source with `ast` and resolves every name and `module.name` in
    it through the function's module globals. A name that is a function
    defined under `backend/` is followed. A name that is an error class (one
    with a `code` and a `status_code` or `close_code`, i.e. the classes in
    each service's `core/errors.py`), or a module-level table of them, counts
    as a refusal the route can produce, unless it only appears in an
    `except` clause, an `isinstance` check or a type annotation. A class
    defined under `backend/` that reachable code constructs has all its
    methods followed, so a method called on an instance is not missed. Every
    branch counts, so this over-approximates; a call through an object whose
    class is never constructed in reachable code is not seen.

Auth
    From what the reachable code reads and refuses:
      * calls `extract_access_token` -> an access token (Bearer header or the
        `access_token` cookie; see the conventions line);
      * refers to the error whose code is `not_an_administrator` -> admin;
      * calls `extract_refresh_token` -> a refresh token;
      * contains the string constant "X-Service-Key" -> the service key.
    A credential counts as required only if a 401 (or, on the socket, a 4401
    close) is reachable too; otherwise it is shown as optional. Nothing
    reachable -> "none".

Idempotent (the first rule that applies)
    1. The request body (OpenAPI `requestBody`, resolved through
       `components.schemas`) has a property whose name ends in `_key`:
       "yes, by `<field>`" (an idempotency key the server stores).
    2. GET, HEAD, PUT, DELETE or OPTIONS: "yes (HTTP <method>)", by RFC 9110
       semantics. "; can write" is added when the reachable code calls
       `.commit` (every service commits in its service layer; the request's
       session never commits by itself), as the ledger's first balance read
       and first price read do.
    3. Reachable code never calls `.commit`: "yes (writes nothing)", a POST
       or PATCH that only reads.
    4. The operation's own OpenAPI description says what a repeat does (regex
       `REPEAT_PATTERN`): "repeat-safe (route doc)". The route's documented
       promise, read from the app, not a key the server checks.
    5. Otherwise "no key": the route takes no idempotency key and its doc
       promises nothing about a repeat. That is not a claim that a repeat
       does harm: a second `publish` is refused, a second `logout` is a
       no-op. The socket is "n/a".

Main error codes
    Where the route declares `responses=` (read from the route object, so
    the decorator's own entries, not FastAPI's automatic 422): each declared
    status from 400 up shows the error codes its description names in
    backticks, keeping only words that are codes of that service's error
    classes. A status declared in prose without a code shows the walk's codes
    with that status; when the walk has more than one, the group is marked ‡,
    since the walk cannot tell which of them the route really returns. A 422
    declared with a response `model` is the ledger's request-validation
    envelope and is not filled from the walk.

    Where the route declares nothing: the walk's codes, marked †.

    Left out everywhere, because the Auth column and the conventions line
    already say them: 401/4401 `invalid_token`, 403 `not_an_administrator`,
    and the framework's 422; and 500s (server faults a client cannot act
    on). On the socket, an error class that inherits its close code from the
    service's base error is answered in band as an error frame and closes
    nothing, so it is shown as "error frame".

    The full table's cross-check section lists disagreements between the
    declaration and the walk: codes the walk reaches that the route does not
    declare, and declared codes the walk did not reach. A status declared
    with no code named is not compared.

No entry in either table comes from `docs/api/*.md`. Those files are only
read for the cross-check section: routes with no `METHOD /path` heading
there, headings that are not a route, and derived codes they never mention.
The conventions line is prose written in this script (`_conventions`), not
generated; every claim in it is visible in the services' `controller/`.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable
from pathlib import Path

PROPOSAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROPOSAL_DIR.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
API_DOCS_DIR = REPO_ROOT / "docs" / "api"

FULL_TABLE_PATH = PROPOSAL_DIR / "api-endpoints.md"
BODY_TABLE_PATH = PROPOSAL_DIR / "api-endpoints-body.md"
APPENDIX_TABLE_PATH = PROPOSAL_DIR / "api-endpoints-appendix.md"
BODY_ERRORS_LIMIT = 60

SHORT_CONVENTIONS = (
    "A token is the access JWT, as a Bearer header or an `httpOnly` cookie. A "
    "refusal is `{\"error\": {\"code\", \"message\"}}`; a bad token is 401 "
    "(4401 on the socket), a trader on an admin route 403. \"No key\" means no "
    "idempotency key and no stated promise about a repeat. A GET marked \"can "
    "write\" does so once: a first balance read mints the signup grant (ADR "
    "0009), a first snapshot opens the market's book."
)

# Table order: identity, then the market's life, then money, then the two readers.
SERVICES = ["auth", "market", "ledger", "audit", "realtime"]

# The audit service has no virtualenv; market's pins the same libraries.
VENV_FALLBACKS = {"audit": "market"}

# Placeholders for the settings that have no default. Never real values: each
# only has to pass its field's validation (lengths from `core/config.py`).
PLACEHOLDER_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://user:<PASSWORD>@localhost:5432/db",
    "JWT_SECRET": "placeholder-not-a-key",
    "LEDGER_SERVICE_KEY": "x" * 32,
    "REDIS_URL": "redis://localhost:6379/0",
}

HTTP_IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}

# "A repeat appends nothing", "a repeated request is the same outcome".
REPEAT_PATTERN = re.compile(r"\b[Aa] repeat(ed)?\b")

ACCESS_TOKEN_READER = "extract_access_token"
REFRESH_TOKEN_READER = "extract_refresh_token"
SERVICE_KEY_HEADER = "X-Service-Key"
COMMIT_METHOD = "commit"
ADMIN_REFUSAL_CODE = "not_an_administrator"
TOKEN_REFUSAL_CODE = "invalid_token"

# The routes that tell the design story, in a market's order: a session, a
# market drafted and published, trading against it with live prices, then
# closing, resolving and paying out.
BODY_ROUTES = [
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("POST", "/markets"),
    ("POST", "/markets/{market_id}/publish"),
    ("GET", "/ledger/balances/me"),
    ("POST", "/ledger/markets/{market_id}/trades"),
    ("POST", "/ledger/markets/{market_id}/orders"),
    ("GET", "/ledger/markets/{market_id}/snapshot"),
    ("WS", "/ws/prices"),
    ("POST", "/markets/{market_id}/approve-outcome"),
    ("POST", "/ledger/markets/{market_id}/settlement"),
]

DOC_HEADING = re.compile(r"^#{2,4} (GET|POST|PUT|PATCH|DELETE) (/\S+)(?: and (/\S+))?")
BACKTICKED_WORD = re.compile(r"`([a-z][a-z0-9_]*)`")

GENERATED_NOTICE = (
    "<!-- Generated by docs/proposal/api_table.py from each service's app. "
    "Do not edit by hand; rerun it. -->"
)
WALK_ONLY_FOOTNOTE = (
    "† The route declares no `responses=`, so these codes come only from the "
    "walk over the code it can reach."
)
AMBIGUOUS_FOOTNOTE = (
    "‡ The route declares this status in prose without naming a code, and "
    "the code it can reach holds more than one error with that status. All "
    "are listed; the walk counts every branch, so some may never come back "
    "from this route."
)
CROSS_CHECK_INTRO = (
    "Generated alongside the table. For each route: error codes the code can "
    "reach that its `responses=` does not declare, codes it declares that the "
    "walk did not reach, and anything `docs/api/` does not mention. Nothing "
    "here changes the table above."
)


# ---------------------------------------------------------------------------
# Collect mode: runs inside one service's virtualenv, prints JSON.
# ---------------------------------------------------------------------------


def _flatten_routes(routes: list) -> list:
    """Every endpoint route, looking inside included routers.

    FastAPI 0.141 keeps an included router as one `_IncludedRouter` entry whose
    `original_router.routes` hold the real routes with their full paths.
    """
    flat = []
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            flat.extend(_flatten_routes(included.routes))
        elif hasattr(route, "dependant"):
            flat.append(route)
    return flat


def _dependency_callables(dependant) -> list:
    """Every callable in a route's resolved `Depends(...)` tree."""
    callables = []
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            callables.append(dependency.call)
        callables.extend(_dependency_callables(dependency))
    return callables


def _is_backend_object(obj: object) -> bool:
    try:
        source_file = inspect.getsourcefile(obj)
    except TypeError:
        return False
    if source_file is None:
        return False
    return Path(source_file).resolve().is_relative_to(BACKEND_DIR)


def _is_error_class(obj: object) -> bool:
    return (
        inspect.isclass(obj)
        and issubclass(obj, BaseException)
        and isinstance(getattr(obj, "code", None), str)
        and (hasattr(obj, "status_code") or hasattr(obj, "close_code"))
        and _is_backend_object(obj)
    )


def _resolve(node: ast.expr, namespace: dict) -> object | None:
    """The object a `name` or `module.name` expression refers to, if knowable.

    Attribute chains are followed only through modules and classes, so
    resolving never runs a property on some live object.
    """
    if isinstance(node, ast.Name):
        return namespace.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, namespace)
        if inspect.ismodule(base) or inspect.isclass(base):
            return getattr(base, node.attr, None)
    return None


def _parsed_source(func) -> ast.AST | None:
    try:
        return ast.parse(textwrap.dedent(inspect.getsource(func)))
    except (OSError, TypeError, SyntaxError):
        return None


def _methods_of(cls: type) -> list:
    methods = []
    for value in vars(cls).values():
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if inspect.isfunction(value):
            methods.append(value)
    return methods


def _non_value_nodes(tree: ast.AST) -> set[int]:
    """Nodes that name a class without using it: annotations, `except` types, type checks.

    `except MarketNotFound:` and `isinstance(exc, InsufficientFunds)` mention
    an error without producing one, so they must not count as a refusal.
    """
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            roots.append(node.type)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("isinstance", "issubclass"):
                roots.extend(node.args[1:])

    excluded = set()
    for root in roots:
        for node in ast.walk(root):
            excluded.add(id(node))
    return excluded


def _error_classes_in(value: object) -> list:
    """The error classes a referenced object is, or holds (a refusal table)."""
    if _is_error_class(value):
        return [value]
    if isinstance(value, dict):
        candidates = list(value.values())
    elif isinstance(value, (list, tuple, set, frozenset)):
        candidates = list(value)
    else:
        return []
    return [candidate for candidate in candidates if _is_error_class(candidate)]


def _walk_reachable(seeds: list) -> dict:
    """What the code reachable from `seeds` refers to.

    Returns the error classes referenced as values (raised, constructed,
    passed along, or held in a module-level table the code reads), the names
    of every function visited, every string constant, and every attribute
    name (`.commit` among them).
    """
    pending = [inspect.unwrap(seed) for seed in seeds]
    visited_code = set()
    expanded_classes = set()
    error_classes = set()
    function_names = set()
    string_constants = set()
    attribute_names = set()

    while pending:
        func = pending.pop()
        if not inspect.isfunction(func) or not _is_backend_object(func):
            continue
        if func.__code__ in visited_code:
            continue
        visited_code.add(func.__code__)
        function_names.add(func.__name__)

        tree = _parsed_source(func)
        if tree is None:
            continue
        namespace = dict(func.__globals__)
        namespace.update(inspect.getclosurevars(func).nonlocals)
        excluded = _non_value_nodes(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                string_constants.add(node.value)
            if isinstance(node, ast.Attribute):
                attribute_names.add(node.attr)
            if id(node) in excluded or not isinstance(node, (ast.Name, ast.Attribute)):
                continue
            target = _resolve(node, namespace)
            if target is None:
                continue
            error_classes.update(_error_classes_in(target))
            function = inspect.unwrap(target)
            if inspect.isfunction(function):
                pending.append(function)

        # A backend class the code constructs: its methods may be called on
        # the instance, which the name lookup above cannot see.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _resolve(node.func, namespace)
            if inspect.isclass(target) and _is_backend_object(target) and not _is_error_class(target):
                if target not in expanded_classes:
                    expanded_classes.add(target)
                    pending.extend(_methods_of(target))

    return {
        "error_classes": error_classes,
        "function_names": function_names,
        "string_constants": string_constants,
        "attribute_names": attribute_names,
    }


def _own_close_code(error_class: type) -> int | None:
    """The socket close code, unless it is only the base class's default.

    The realtime service's protocol errors inherit `RealtimeError`'s 1008 but
    are answered in band as error frames and close nothing, so a close code
    counts only when a class below the service's base error sets it.
    """
    backend_bases = [cls for cls in error_class.__mro__ if _is_backend_object(cls)]
    service_base = backend_bases[-1]
    for cls in backend_bases:
        if "close_code" in vars(cls):
            return None if cls is service_base else cls.close_code
    return None


def _error_record(error_class: type) -> dict:
    is_socket_error = hasattr(error_class, "close_code")
    return {
        "name": error_class.__name__,
        "code": error_class.code,
        "status": getattr(error_class, "status_code", None),
        "close_code": _own_close_code(error_class) if is_socket_error else None,
        "in_band": is_socket_error and _own_close_code(error_class) is None,
    }


def _service_error_codes() -> list[str]:
    """Every error code the service's `core/errors.py` defines."""
    import core.errors

    codes = []
    for value in vars(core.errors).values():
        if _is_error_class(value):
            codes.append(value.code)
    return sorted(set(codes))


def _body_fields(operation: dict, spec: dict) -> list[str]:
    """The top-level property names of an operation's JSON request body."""
    content = operation.get("requestBody", {}).get("content", {})
    schema = content.get("application/json", {}).get("schema", {})
    reference = schema.get("$ref")
    if reference is not None:
        schema_name = reference.rsplit("/", 1)[-1]
        schema = spec["components"]["schemas"][schema_name]
    return list(schema.get("properties", {}))


def _route_record(route, method: str, path: str, operation: dict | None, spec: dict) -> dict:
    seeds = _dependency_callables(route.dependant) + [route.endpoint]
    reachable = _walk_reachable(seeds)
    declared = {}
    for status, entry in (getattr(route, "responses", None) or {}).items():
        model = entry.get("model")
        declared[str(status)] = {
            "description": entry.get("description", ""),
            "model": model.__name__ if model is not None else None,
        }
    operation = operation or {}
    return {
        "method": method,
        "path": path,
        "description": operation.get("description", ""),
        "tags": operation.get("tags", []),
        "body_fields": _body_fields(operation, spec) if operation else [],
        "declared_responses": declared,
        "errors": [_error_record(cls) for cls in reachable["error_classes"]],
        "reads_access_token": ACCESS_TOKEN_READER in reachable["function_names"],
        "reads_refresh_token": REFRESH_TOKEN_READER in reachable["function_names"],
        "reads_service_key": SERVICE_KEY_HEADER in reachable["string_constants"],
        "commits": COMMIT_METHOD in reachable["attribute_names"],
    }


def collect(service: str) -> dict:
    """Every route of one service, and its error codes, as plain data.

    Run inside the service's virtualenv. Routes come out in registration
    order, which is the order `/docs` lists them in.
    """
    import main  # the service's composition root, found through PYTHONPATH
    from fastapi.exceptions import RequestValidationError

    app = main.app
    spec = app.openapi()
    # operationId -> every (method, path, operation) that carries it.
    operations_by_id: dict[str, list] = {}
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            entry = (method.upper(), path, operation)
            operations_by_id.setdefault(operation["operationId"], []).append(entry)

    records = []
    matched_ids = set()
    for route in _flatten_routes(app.routes):
        if getattr(route, "methods", None) is None:
            records.append(_route_record(route, "WS", route.path, None, spec))
            continue
        # Absent for a route with include_in_schema=False: not in the contract.
        for method, path, operation in operations_by_id.get(route.unique_id, []):
            records.append(_route_record(route, method, path, operation, spec))
            matched_ids.add(route.unique_id)

    unmatched = sorted(set(operations_by_id) - matched_ids)
    if unmatched:
        raise SystemExit(f"{service}: OpenAPI operations with no route: {unmatched}")

    for record in records:
        record["service"] = service
    return {
        "routes": records,
        "error_codes": _service_error_codes(),
        # FastAPI always registers its own; only a handler from `backend/` wraps.
        "wraps_validation_errors": _is_backend_object(
            app.exception_handlers.get(RequestValidationError)
        ),
    }


# ---------------------------------------------------------------------------
# Render mode: system Python, spawns the collectors, writes markdown.
# ---------------------------------------------------------------------------


def _venv_python(venvs_dir: Path, service: str) -> Path:
    for owner in [service, VENV_FALLBACKS.get(service)]:
        if owner is None:
            continue
        venv = venvs_dir / f"{owner}_service" / ".venv"
        for candidate in [venv / "Scripts" / "python.exe", venv / "bin" / "python"]:
            if candidate.exists():
                return candidate
    raise SystemExit(f"no virtualenv for {service}_service under {venvs_dir}")


def _run_collector(service: str, venvs_dir: Path) -> dict:
    environment = dict(os.environ)
    environment.update(PLACEHOLDER_ENV)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(BACKEND_DIR / f"{service}_service"), str(BACKEND_DIR)]
    )
    command = [
        str(_venv_python(venvs_dir, service)),
        str(Path(__file__).resolve()),
        "--collect",
        service,
    ]
    # An empty working directory, so pydantic-settings finds no `.env`.
    with tempfile.TemporaryDirectory() as empty_dir:
        completed = subprocess.run(
            command, cwd=empty_dir, env=environment, capture_output=True, text=True
        )
    if completed.returncode != 0:
        raise SystemExit(f"collecting {service} failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


# --- Auth and idempotency -----------------------------------------------------


def _requires_credential(row: dict) -> bool:
    for error in row["errors"]:
        if error["status"] == 401 or error["close_code"] == 4401:
            return True
    return False


def _auth_label(row: dict) -> str:
    credentials = []
    if row["reads_access_token"]:
        refuses_traders = any(error["code"] == ADMIN_REFUSAL_CODE for error in row["errors"])
        if refuses_traders:
            credentials.append("admin token")
        else:
            credentials.append("token, any role")
    if row["reads_refresh_token"]:
        credentials.append("refresh token")
    if row["reads_service_key"]:
        credentials.append("`X-Service-Key`")

    if not credentials:
        return "none"
    label = " + ".join(credentials)
    if not _requires_credential(row):
        label += " (optional)"
    return label


def _idempotency_label(row: dict) -> str:
    if row["method"] == "WS":
        return "n/a"
    for field in row["body_fields"]:
        if field.endswith("_key"):
            return f"yes, by `{field}`"
    if row["method"] in HTTP_IDEMPOTENT_METHODS:
        if row["commits"]:
            return f"yes (HTTP {row['method']}; can write)"
        return f"yes (HTTP {row['method']})"
    if not row["commits"]:
        return "yes (writes nothing)"
    if REPEAT_PATTERN.search(row["description"]):
        return "repeat-safe (route doc)"
    return "no key"


# --- Error codes --------------------------------------------------------------


def _status_of(error: dict) -> str:
    """An HTTP status, a socket close code, or "frame" for an in-band error."""
    if error["in_band"]:
        return "frame"
    if error["status"] is not None:
        return str(error["status"])
    return str(error["close_code"])


def _status_order(status: str) -> int:
    return int(status) if status.isdigit() else 10_000


def _is_left_out_status(status: str) -> bool:
    """500s are server faults; 503 stays, as it names a dependency that is down."""
    return status.isdigit() and 500 <= int(status) < 600 and status != "503"


def _is_left_out_code(code: str) -> bool:
    """The two refusals the Auth column and the conventions line already state."""
    return code in (TOKEN_REFUSAL_CODE, ADMIN_REFUSAL_CODE)


def _walk_codes(row: dict) -> dict[str, set[str]]:
    """The reachable-code walk's errors, by status, less the left-out ones."""
    codes_by_status: dict[str, set[str]] = {}
    for error in row["errors"]:
        status = _status_of(error)
        if _is_left_out_status(status) or _is_left_out_code(error["code"]):
            continue
        codes_by_status.setdefault(status, set()).add(error["code"])
    return codes_by_status


def _declared_codes(row: dict, error_codes: dict) -> dict[str, set[str]]:
    """Each declared error status, with the known codes its description names."""
    codes_by_status = {}
    for status, entry in row["declared_responses"].items():
        if int(status) < 400 or _is_left_out_status(status):
            continue
        named = set()
        for word in BACKTICKED_WORD.findall(entry["description"]):
            if word in error_codes and not _is_left_out_code(word):
                named.add(word)
        codes_by_status[status] = named
    return codes_by_status


def _prose_only_statuses(row: dict, error_codes: dict) -> set[str]:
    """Declared statuses whose description names no error code.

    Not a 422 declared with a response `model`: that is the ledger's
    request-validation envelope (`ValidationErrorOut`), which the conventions
    line covers, so the walk's 422s are not offered in its place.
    """
    statuses = set()
    for status, named in _declared_codes(row, error_codes).items():
        is_validation_envelope = (
            status == "422" and row["declared_responses"][status]["model"] is not None
        )
        if not named and not is_validation_envelope:
            statuses.add(status)
    return statuses


def _shown_codes(row: dict, error_codes: dict) -> dict[str, set[str]]:
    """What the table shows: the declaration where there is one, else the walk.

    A declared status whose description names codes shows those codes; one
    declared in prose only shows what the walk found with that status. A
    status the walk found but the route does not declare is not shown (the
    cross-check lists it).
    """
    walk = _walk_codes(row)
    if not row["declared_responses"]:
        return walk

    prose_only = _prose_only_statuses(row, error_codes)
    shown = {}
    for status, named in _declared_codes(row, error_codes).items():
        if named:
            shown[status] = named
        elif status in prose_only and walk.get(status):
            shown[status] = walk[status]
    return shown


def _ambiguous_statuses(row: dict, error_codes: dict) -> set[str]:
    """Prose-only statuses for which the walk found more than one code.

    One code is a reliable mapping: the route says the status happens and
    exactly one reachable error has it. Several cannot be told apart, and the
    walk counts every branch, so some may never come back from this route.
    """
    if not row["declared_responses"]:
        return set()
    walk = _walk_codes(row)
    ambiguous = set()
    for status in _prose_only_statuses(row, error_codes):
        if len(walk.get(status, set())) > 1:
            ambiguous.add(status)
    return ambiguous


def _errors_label(row: dict, error_codes: dict, *, list_ambiguous: bool) -> str:
    """The error column; `list_ambiguous=False` shows a ‡ group as its status only."""
    shown = _shown_codes(row, error_codes)
    if not shown:
        return "—"
    ambiguous = _ambiguous_statuses(row, error_codes)
    parts = []
    for status in sorted(shown, key=_status_order):
        prefix = "error frame" if status == "frame" else status
        if status in ambiguous and not list_ambiguous:
            parts.append(f"{prefix} ‡")
            continue
        codes = ", ".join(f"`{code}`" for code in sorted(shown[status]))
        marker = " ‡" if status in ambiguous else ""
        parts.append(f"{prefix} {codes}{marker}")
    label = "; ".join(parts)
    if not row["declared_responses"]:
        label += " †"
    return label


# --- Tables -------------------------------------------------------------------


def _statuses_only(errors: str) -> str:
    """`404 `x`; 409 `a`, `b`` becomes `404, 409`, for the one-page appendix."""
    statuses = re.findall(r"(?:^|; )(\d{3,4}|error frame)", errors)
    return ", ".join(statuses) or "—"


def _first_codes(errors: str) -> str:
    """Every status, with its codes while BODY_ERRORS_LIMIT lasts, for the body."""
    cleaned = re.sub(r" [†‡]", "", errors)
    shown = []
    used = 0
    for section in cleaned.split("; "):
        if used + len(section) <= BODY_ERRORS_LIMIT:
            shown.append(section)
            used += len(section)
        else:
            status = section.partition(" `")[0]
            shown.append(f"{status} …")
    return "; ".join(shown)


def _table_lines(
    rows: list[dict],
    error_codes_by_service: dict,
    *,
    list_ambiguous: bool,
    shorten_errors: Callable[[str], str] | None = None,
) -> list[str]:
    lines = [
        "| Service | Method | Path | Auth | Idempotent | Main error codes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        error_codes = error_codes_by_service[row["service"]]
        errors = _errors_label(row, error_codes, list_ambiguous=list_ambiguous)
        if shorten_errors is not None:
            errors = shorten_errors(errors)
        lines.append(
            f"| {row['service']} | {row['method']} | `{row['path']}` | "
            f"{_auth_label(row)} | {_idempotency_label(row)} | {errors} |"
        )
    if shorten_errors is not None:
        return lines
    footnotes = []
    if any(not row["declared_responses"] for row in rows):
        footnotes.append(WALK_ONLY_FOOTNOTE)
    if any(_ambiguous_statuses(row, error_codes_by_service[row["service"]]) for row in rows):
        footnotes.append(AMBIGUOUS_FOOTNOTE)
    for footnote in footnotes:
        lines.extend(["", footnote])
    return lines


def _conventions(all_rows: list[dict], services_wrapping_422: list[str]) -> str:
    """The one paragraph of rules every route shares. Prose, with two derived parts.

    Which services serve `GET /health`, and which register their own handler
    for FastAPI's `RequestValidationError`, come from the apps.
    """
    health_services = []
    for service in SERVICES:
        if any(row["service"] == service and "ops" in row["tags"] for row in all_rows):
            health_services.append(service)
    if len(health_services) == len(SERVICES):
        health = "Every service also serves an unauthenticated `GET /health`."
    else:
        health = f"An unauthenticated `GET /health` is on {', '.join(health_services)}."

    validation = ""
    if services_wrapping_422:
        verb = "answers" if len(services_wrapping_422) == 1 else "answer"
        validation = (
            f", except on {', '.join(services_wrapping_422)}, which {verb} it "
            "in the same envelope as `invalid_request`"
        )

    return (
        "**Conventions shared by every route.** A *token* is the access JWT: a "
        "service sends `Authorization: Bearer <jwt>`, a browser the `httpOnly` "
        "`access_token` cookie, and the header wins. The socket reads the "
        "same two, in practice the cookie, since a browser cannot set headers "
        "on a WebSocket. A *refresh token* is the `refresh_token` cookie or an "
        "`X-Refresh-Token` header. Every domain refusal is "
        "`{\"error\": {\"code\", \"message\"}}`, plus `details` when it "
        "names fields: a missing or bad token is `401 invalid_token` (the socket closes 4401), a trader "
        "on an admin route `403 not_an_administrator` (auth checks the role on "
        "its own user row, the other services in the token's claim). A "
        f"malformed request is FastAPI's `422 {{\"detail\": [...]}}`{validation}. "
        f"{health} \"No key\" means no idempotency key and no stated promise "
        "about a repeat: a repeat may be refused, run again or do nothing."
    )


# --- Cross-check --------------------------------------------------------------


def _normalised(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _documented_operations(service: str) -> tuple[set[tuple[str, str]], str]:
    """`METHOD /path` headings in `docs/api/<service>-service.md`, and its text."""
    doc_path = API_DOCS_DIR / f"{service}-service.md"
    if not doc_path.exists():
        return set(), ""
    text = doc_path.read_text(encoding="utf-8")
    documented = set()
    for line in text.splitlines():
        match = DOC_HEADING.match(line)
        if match is None:
            continue
        method, path, sibling = match.groups()
        documented.add((method, _normalised(path)))
        if sibling is not None:
            # "GET /ledger/users/{user_id}/balance and /entries"
            documented.add((method, _normalised(path.rsplit("/", 1)[0] + sibling)))
    return documented, text


def _format_codes(codes_by_status: dict[str, set[str]]) -> str:
    parts = []
    for status in sorted(codes_by_status, key=_status_order):
        for code in sorted(codes_by_status[status]):
            parts.append(f"{status} `{code}`")
    return ", ".join(parts)


def _declaration_notes(row: dict, error_codes: dict) -> list[str]:
    """Where the route's `responses=` and the reachable-code walk disagree."""
    if not row["declared_responses"]:
        return []
    declared = _declared_codes(row, error_codes)
    walk = _walk_codes(row)
    all_walk_statuses = {_status_of(error) for error in row["errors"]}
    notes = []

    undeclared: dict[str, set[str]] = {}
    for status, codes in walk.items():
        for code in codes:
            named = declared.get(status)
            if named is None or (named and code not in named):
                undeclared.setdefault(status, set()).add(code)
    if undeclared:
        notes.append(f"reachable but not declared: {_format_codes(undeclared)}")

    unreached = set()
    walk_codes = set()
    for codes in walk.values():
        walk_codes.update(codes)
    for named in declared.values():
        unreached |= named - walk_codes
    if unreached:
        listed = ", ".join(f"`{code}`" for code in sorted(unreached))
        notes.append(f"declared but not reached by the walk: {listed}")

    unexplained = []
    for status, named in declared.items():
        if not named and status not in all_walk_statuses and status != "422":
            unexplained.append(status)
    if unexplained:
        notes.append(
            f"declares {', '.join(sorted(unexplained))} with no code named and "
            "no reachable error of that status"
        )
    return notes


def _documentation_notes(row: dict, documented: set, doc_text: str, doc_name: str) -> list[str]:
    """Where the route and `docs/api/<service>-service.md` disagree."""
    notes = []
    if row["method"] == "WS":
        if row["path"] not in doc_text:
            notes.append(f"not mentioned in `{doc_name}`")
    elif (row["method"], _normalised(row["path"])) not in documented:
        notes.append(f"no `{row['method']} /path` heading for it in `{doc_name}`")

    unmentioned = []
    for error in row["errors"]:
        if _is_left_out_status(_status_of(error)):
            continue
        mentioned = error["code"] in doc_text
        if error["close_code"] is not None and str(error["close_code"]) in doc_text:
            mentioned = True
        if not mentioned:
            unmentioned.append(f"`{error['code']}`")
    if unmentioned:
        notes.append(f"codes never mentioned in `{doc_name}`: {', '.join(sorted(unmentioned))}")
    return notes


def _cross_check_lines(rows: list[dict], error_codes_by_service: dict) -> list[str]:
    lines = []
    for service in SERVICES:
        doc_name = f"docs/api/{service}-service.md"
        documented, doc_text = _documented_operations(service)
        served = set()
        for row in rows:
            if row["service"] != service:
                continue
            served.add((row["method"], _normalised(row["path"])))
            notes = _declaration_notes(row, error_codes_by_service[service])
            notes += _documentation_notes(row, documented, doc_text, doc_name)
            if notes:
                lines.append(f"- `{row['method']} {row['path']}`: {'; '.join(notes)}.")
        for method, path in sorted(documented - served):
            lines.append(f"- `{method} {path}`: a heading in `{doc_name}`, but not a route of the app.")
    return lines


# --- Documents ----------------------------------------------------------------


def _full_document(rows: list[dict], conventions: str, error_codes_by_service: dict) -> str:
    counts = []
    for service in SERVICES:
        counts.append(f"{service} {sum(1 for row in rows if row['service'] == service)}")
    lines = [
        "# API endpoints (full)",
        "",
        GENERATED_NOTICE,
        "",
        f"{len(rows)} endpoints across five services ({', '.join(counts)}), "
        "generated from each service's `app.openapi()` and its routes. How "
        "each column is derived is in the docstring of "
        "`docs/proposal/api_table.py`.",
        "",
        conventions,
        "",
    ]
    lines.extend(_table_lines(rows, error_codes_by_service, list_ambiguous=True))
    lines.extend(["", "## Cross-check", "", CROSS_CHECK_INTRO, ""])
    lines.extend(_cross_check_lines(rows, error_codes_by_service) or ["- Nothing disagrees."])
    lines.append("")
    return "\n".join(lines)


def _appendix_document(rows: list[dict], error_codes_by_service: dict) -> str:
    lines = [
        GENERATED_NOTICE,
        "",
        f"All {len(rows)} endpoints, with the error statuses each can answer. "
        f"{SHORT_CONVENTIONS}",
        "",
    ]
    lines.extend(
        _table_lines(
            rows, error_codes_by_service, list_ambiguous=False, shorten_errors=_statuses_only
        )
    )
    lines.append("")
    return "\n".join(lines)


def _body_document(rows: list[dict], error_codes_by_service: dict) -> str:
    rows_by_key = {(row["method"], row["path"]): row for row in rows}
    chosen = []
    for key in BODY_ROUTES:
        if key not in rows_by_key:
            raise SystemExit(f"BODY_ROUTES names {key}, which no service serves")
        chosen.append(rows_by_key[key])

    lines = [
        GENERATED_NOTICE,
        "",
        f"{len(chosen)} of the {len(rows)} endpoints, following a market from "
        f"creation to payout; all of them are in the appendix. {SHORT_CONVENTIONS}",
        "",
    ]
    lines.extend(
        _table_lines(
            chosen, error_codes_by_service, list_ambiguous=False, shorten_errors=_first_codes
        )
    )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--collect", choices=SERVICES, help=argparse.SUPPRESS)
    parser.add_argument(
        "--venvs",
        type=Path,
        default=BACKEND_DIR,
        help="directory holding <service>_service/.venv (default: this checkout's backend/)",
    )
    arguments = parser.parse_args()

    if arguments.collect is not None:
        json.dump(collect(arguments.collect), sys.stdout)
        return

    all_rows = []
    error_codes_by_service = {}
    services_wrapping_422 = []
    for service in SERVICES:
        collected = _run_collector(service, arguments.venvs)
        all_rows.extend(collected["routes"])
        error_codes_by_service[service] = collected["error_codes"]
        if collected["wraps_validation_errors"]:
            services_wrapping_422.append(service)
    rows = [row for row in all_rows if "ops" not in row["tags"]]
    conventions = _conventions(all_rows, services_wrapping_422)

    FULL_TABLE_PATH.write_text(
        _full_document(rows, conventions, error_codes_by_service), encoding="utf-8"
    )
    BODY_TABLE_PATH.write_text(
        _body_document(rows, error_codes_by_service), encoding="utf-8"
    )
    APPENDIX_TABLE_PATH.write_text(
        _appendix_document(rows, error_codes_by_service), encoding="utf-8"
    )
    print(f"{FULL_TABLE_PATH.name}: {len(rows)} rows")
    print(f"{BODY_TABLE_PATH.name}: {len(BODY_ROUTES)} rows")
    print(f"{APPENDIX_TABLE_PATH.name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
