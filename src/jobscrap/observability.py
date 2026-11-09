"""Shared logging setup and context; this module has no third-party dependencies.

Modules obtain their own logger with ``logging.getLogger(__name__)``. Only the
CLI configures handlers, so importing library code never changes logging policy.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from pathlib import Path
import sys
import time
import traceback

_context = ContextVar("jobscrap_log_context", default=None)
_FIELDS = ("run_id", "company_id", "operation", "job_id")


@contextmanager
def log_context(**fields):
    """Attach trusted identifiers to logs in this execution context, then restore it.

    Nested company/job scopes inherit the run ID. ContextVar keeps independent
    worker threads from accidentally attributing records to another run.
    """
    if set(fields) - set(_FIELDS):
        raise ValueError("Unsupported log context field")
    values = {**(_context.get() or {}), **fields}
    token = _context.set(values)
    try:
        yield values
    finally:
        _context.reset(token)


class ContextFilter(logging.Filter):
    """Populate formatter fields even for events outside a pipeline run."""

    def filter(self, record):
        values = _context.get() or {}
        for field in _FIELDS:
            value = getattr(record, field, values.get(field, "-"))
            # IDs should be simple values; escape line breaks to keep one event per line.
            setattr(record, field, str(value).replace("\r", r"\r").replace("\n", r"\n"))
        return True


def _frames(tb):
    """Return locations only: no exception messages, source lines or local variables."""
    return " > ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(tb)[-8:]
    ) or "unavailable"


class UTCFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record):
        # Another handler may have cached a raw exception string on this record.
        # Reformat it safely here without changing what other handlers receive.
        previous = record.exc_text
        record.exc_text = None
        try:
            return super().format(record)
        finally:
            record.exc_text = previous

    def formatException(self, exc_info):
        # Also protect future log.exception() calls: SDK exception messages can
        # contain credentials, request bodies, or an entire database connection URI.
        return f"error_type={exc_info[0].__name__} frames={_frames(exc_info[2])}"


def configure_logging(level="INFO"):
    """Configure one application handler on stderr, leaving stdout for CLI results.

    Journald handles persistence/rotation on the VM. A shared rotating file would
    need additional coordination between the dashboard and scheduled processes.
    """
    logger = logging.getLogger("jobscrap")
    logger.setLevel(level)
    logger.propagate = False
    # Reconfiguration is safe: keep one owned handler instead of duplicating lines.
    for handler in list(logger.handlers):
        if handler.get_name() == "jobscrap-console":
            logger.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name("jobscrap-console")
    handler.addFilter(ContextFilter())
    handler.setFormatter(UTCFormatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s "
        "run_id=%(run_id)s company_id=%(company_id)s operation=%(operation)s "
        "job_id=%(job_id)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    # Avoid verbose third-party request traces when the app is set to DEBUG.
    for name in ("httpx", "httpcore", "pymongo", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_failure(logger, event, exc, *, level=logging.ERROR):
    """Emit a safe error classification plus optional DEBUG failure locations.

    Never interpolate ``exc`` or enable a raw traceback formatter at call sites.
    The normal event identifies the failure; DEBUG helps locate it in the code.
    """
    logger.log(level, "%s error_type=%s", event, type(exc).__name__)
    logger.debug("failure_location event=%s frames=%s", event, _frames(exc.__traceback__))
