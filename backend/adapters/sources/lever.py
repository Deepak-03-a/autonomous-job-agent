"""Public Lever postings adapter."""

from datetime import datetime

import httpx

from adapters.sources.protocol import SourcePolicy
from core.domain import JobPosting


class LeverSource:
    def __init__(self, site: str, company: str, careers_url: str, company_website: str):
        self.site = site
        self.company = company
        self.careers_url = careers_url
        self.company_website = company_website
        self.policy = SourcePolicy(company, 15, 4, careers_url, check_robots=True)

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        del since
        response = httpx.get(
            f"https://api.lever.co/v0/postings/{self.site}",
            params={"mode": "json"},
            timeout=20,
        )
        response.raise_for_status()
        return [
            JobPosting(
                source=self.company,
                external_id=str(item.get("id", item.get("hostedUrl", ""))),
                company=self.company,
                title=item.get("text", ""),
                location=(item.get("categories") or {}).get("location", ""),
                description=item.get("descriptionPlain", ""),
                posted_at=_parse_epoch(item.get("createdAt")),
                metadata={
                    "company_website": self.company_website,
                    "careers_url": self.careers_url,
                    "application_url": item.get("applyUrl") or item.get("hostedUrl", ""),
                    "provider": "lever",
                },
            )
            for item in response.json()
        ]


def _parse_epoch(value):
    return datetime.fromtimestamp(value / 1000) if value else None
