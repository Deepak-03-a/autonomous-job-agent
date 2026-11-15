"""Public Ashby job-board API adapter."""

from datetime import datetime

import httpx

from adapters.sources.protocol import SourcePolicy
from core.domain import JobPosting


class AshbySource:
    def __init__(self, board: str, company: str, careers_url: str, company_website: str):
        self.board = board
        self.company = company
        self.careers_url = careers_url
        self.company_website = company_website
        self.policy = SourcePolicy(company, 15, 4, careers_url, check_robots=False)

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        del since
        response = httpx.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{self.board}",
            params={"includeCompensation": "true"}, timeout=20,
        )
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
        return [posting for item in jobs if (posting := self._posting(item)) is not None]

    def _posting(self, item: dict) -> JobPosting | None:
        title = str(item.get("title") or "").strip()
        job_url = str(item.get("jobUrl") or item.get("applyUrl") or "").strip()
        if not title or not job_url:
            return None
        published = _parse_date(item.get("publishedAt"))
        remote = bool(item.get("isRemote")) or str(item.get("workplaceType", "")).casefold() == "remote"
        return JobPosting(
            source=self.company, external_id=str(item.get("id") or job_url),
            company=self.company, title=title,
            location=str(item.get("location") or ""),
            work_mode="remote" if remote else "onsite_or_hybrid",
            description=str(item.get("descriptionPlain") or item.get("descriptionHtml") or ""),
            posted_at=published,
            metadata={
                "company_id": self.company, "country": "", "careers_url": self.careers_url,
                "application_url": job_url, "company_website": self.company_website,
                "provider": "ashby", "is_startup": False,
            },
        )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
