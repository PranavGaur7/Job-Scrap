"""Local process coordination for one Mac/Linux host; locks release after crashes."""
from contextlib import contextmanager
import fcntl
from pathlib import Path


class RunBusy(RuntimeError):
    pass


@contextmanager
def run_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "pipeline.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunBusy("A scrape or rescore is already running") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
