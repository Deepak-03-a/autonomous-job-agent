"""Source contracts used by the ingestion worker."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.domain import JobPosting


@dataclass(frozen=True)
class SourcePolicy:
    name: str
    min_interval_s: int
    max_runs_per_day: int
    attribution: str
    check_robots: bool = False


class JobSource(Protocol):
    policy: SourcePolicy

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        """Fetch only the source's own published openings."""
