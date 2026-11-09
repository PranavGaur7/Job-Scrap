"""Local process coordination for one Mac/Linux host; locks release after crashes."""
from contextlib import contextmanager
import fcntl
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class RunBusy(RuntimeError):
    pass


@contextmanager
def run_lock(state_dir: Path):
    """Serialize pipeline side effects across CLI and dashboard on one host.

    The kernel owns the lock, so a crash releases it without deleting a stale
    sentinel file. All processes must point to the same STATE_DIR.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "pipeline.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("pipeline_lock_busy")
            raise RunBusy("A scrape or rescore is already running") from None
        log.debug("pipeline_lock_acquired")
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            log.debug("pipeline_lock_released")
