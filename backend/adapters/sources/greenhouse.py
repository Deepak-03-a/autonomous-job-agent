"""Public Greenhouse Job Board adapter."""

from datetime import datetime

import httpx

from adapters.sources.protocol import SourcePolicy
from core.domain import JobPosting


class GreenhouseSource:
    def __init__(self, board_token: str, company: str, careers_url: str, company_website: str):
        self.board_token = board_token
        self.company = company
        self.careers_url = careers_url
        self.company_website = company_website
        self.policy = SourcePolicy(company, 15, 4, careers_url, check_robots=True)

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        del since
        response = httpx.get(
            f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs",
            params={"content": "true"},
            timeout=20,
        )
        response.raise_for_status()
        jobs = []
        for item in response.json().get("jobs", []):
            jobs.append(JobPosting(
                source=self.company,
                external_id=str(item["id"]),
                company=self.company,
                title=item.get("title", ""),
                location=(item.get("location") or {}).get("name", ""),
                description=item.get("content", ""),
                posted_at=_parse_date(item.get("updated_at")),
                metadata={
                    "company_website": self.company_website,
                    "careers_url": self.careers_url,
                    "application_url": item.get("absolute_url", ""),
                    "provider": "greenhouse",
                },
            ))
        return jobs


def _parse_date(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
