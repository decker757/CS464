"""JSON log lines carrying the request ID, and the one setup every service calls. #599

One line of JSON per record. Only the fields named here are written: an
allowlist, so a caller's `extra={"password": ...}` can never reach the log.
Never put a token, cookie, Authorization header, query string or body in a
message either; log the route template, not the raw URL.

`configure_logging` is safe to call twice (suites import `main.py` many times)
and never removes a handler it did not install, so pytest's `caplog` keeps
working.
"""

import json
import logging
import sys
from datetime import datetime, timezone

from shared.request_ids import current_request_id

ACCESS_FIELDS = ("method", "route", "status", "duration_ms")


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc)
        line = {
            "timestamp": timestamp.isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service,
            "request_id": current_request_id(),
        }
        for field in ACCESS_FIELDS:
            if hasattr(record, field):
                line[field] = getattr(record, field)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(*, service: str, level: int = logging.INFO) -> None:
    """Send every log line in this process to stdout as JSON, once.

    Changes global logging state: adds one JSON stdout handler to the root
    logger if it has none, and sets the root level; clears the `uvicorn` and
    `uvicorn.error` handlers and makes them propagate to it; disables
    `uvicorn.access`, which the request-ID middleware replaces; and caps
    `httpx` and `httpcore` at WARNING.
    """
    root = logging.getLogger()
    already_configured = any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
    if not already_configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter(service=service))
        root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    # At INFO, httpx logs "HTTP Request: GET <full url>", query string included.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
