from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..domain import JobListing


@dataclass(frozen=True)
class ScrapeBatch:
    jobs: Sequence[JobListing]
    # True only if ALL pages and listings were fetched successfully. This permits
    # closing jobs absent from the snapshot. Incremental/date-limited feeds use False.
    complete_snapshot: bool = False


class Adapter(Protocol):
    name: str

    def allowed_hosts(self, company: dict) -> set[str]: ...
    def validate_config(self, company: dict) -> None: ...
    def fetch(self, company: dict, client) -> ScrapeBatch: ...
