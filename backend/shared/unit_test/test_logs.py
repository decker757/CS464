import json
import logging
import sys

import pytest

from shared.logs import JsonFormatter, configure_logging
from shared.request_ids import background_run

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def restore_logging():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_uvicorn = {
        name: (
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).propagate,
            logging.getLogger(name).disabled,
        )
        for name in UVICORN_LOGGERS
    }
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name, (handlers, propagate, disabled) in saved_uvicorn.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.disabled = disabled


def _format(message="hello", *, extra=None, exc_info=None) -> str:
    logger = logging.getLogger("test.logs")
    record = logger.makeRecord(
        "test.logs", logging.INFO, __file__, 1, message, (), exc_info, extra=extra
    )
    return JsonFormatter(service="ledger").format(record)


def test_a_line_is_one_json_object_with_the_required_fields():
    with background_run() as request_id:
        line = _format()
    parsed = json.loads(line)
    assert {"timestamp", "level", "logger", "message", "service", "request_id"} <= parsed.keys()
    assert parsed["request_id"] == request_id and parsed["service"] == "ledger"
    assert parsed["timestamp"].endswith("+00:00")


def test_an_exception_stays_on_one_line_with_its_traceback():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        line = _format(exc_info=sys.exc_info())
    assert "\n" not in line and "RuntimeError" in json.loads(line)["exception"]


def test_extra_fields_outside_the_allowlist_are_not_written():
    line = _format(extra={"password": "hunter2-x", "route": "/a/{id}"})
    assert "hunter2-x" not in line and json.loads(line)["route"] == "/a/{id}"


def test_access_fields_appear_when_present():
    line = _format(extra={"method": "GET", "route": "/a", "status": 200, "duration_ms": 1.5})
    parsed = json.loads(line)
    assert (parsed["method"], parsed["status"], parsed["duration_ms"]) == ("GET", 200, 1.5)


def test_configuring_twice_adds_one_handler_and_keeps_the_others():
    root = logging.getLogger()
    sentinel = logging.NullHandler()
    root.addHandler(sentinel)
    configure_logging(service="ledger")
    configure_logging(service="ledger")
    assert sentinel in root.handlers
    assert sum(isinstance(h.formatter, JsonFormatter) for h in root.handlers) == 1


def test_a_record_is_written_once_as_json(capsys):
    configure_logging(service="ledger")
    logging.getLogger("test.once").info("only-once")
    lines = [l for l in capsys.readouterr().out.splitlines() if "only-once" in l]
    assert len(lines) == 1 and json.loads(lines[0])["service"] == "ledger"


def test_uvicorn_errors_reach_the_json_handler_and_its_access_log_is_off():
    logging.getLogger("uvicorn.error").addHandler(logging.NullHandler())
    configure_logging(service="ledger")
    assert logging.getLogger("uvicorn.access").disabled
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        assert logger.propagate and not logger.handlers
