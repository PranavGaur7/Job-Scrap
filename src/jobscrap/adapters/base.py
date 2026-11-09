"""Contract separating company-specific extraction from the shared pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..domain import JobListing


@dataclass(frozen=True)
class ScrapeBatch:
    """Fetched listings plus whether absence can safely mean a job has closed."""
    jobs: Sequence[JobListing]
    # True only if ALL pages and listings were fetched successfully. This permits
    # closing jobs absent from the snapshot. Incremental/date-limited feeds use False.
    complete_snapshot: bool = False


class Adapter(Protocol):
    """Translate a proven source into common listings using an injected client.

    This describes expected methods for tools/readers; Protocol does not validate
    a live source or enforce method behavior at runtime.
    """
    name: str

    def allowed_hosts(self, company: dict) -> set[str]: ...
    def validate_config(self, company: dict) -> None: ...
    def fetch(self, company: dict, client) -> ScrapeBatch: ...
