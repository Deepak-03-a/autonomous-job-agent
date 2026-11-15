"""Public SmartRecruiters postings API adapter."""

from datetime import datetime

import httpx

from adapters.sources.protocol import SourcePolicy
from core.domain import JobPosting


class SmartRecruitersSource:
    PAGE_SIZE = 100
    MAX_PAGES = 100

    def __init__(self, company_id: str, company: str, careers_url: str, company_website: str):
        self.company_id = company_id
        self.company = company
        self.careers_url = careers_url
        self.company_website = company_website
        self.policy = SourcePolicy(company, 15, 4, careers_url, check_robots=False)

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        del since
        postings: list[JobPosting] = []
        with httpx.Client(timeout=20) as client:
            for page in range(self.MAX_PAGES):
                offset = page * self.PAGE_SIZE
                response = client.get(
                    f"https://api.smartrecruiters.com/v1/companies/{self.company_id}/postings",
                    params={"limit": self.PAGE_SIZE, "offset": offset},
                )
                response.raise_for_status()
                payload = response.json()
                items = payload.get("content", [])
                postings.extend(posting for item in items if (posting := self._posting(item)) is not None)
                total = int(payload.get("totalFound") or offset + len(items))
                if not items or offset + len(items) >= total:
                    break
        return postings

    def _posting(self, item: dict) -> JobPosting | None:
        title = str(item.get("name") or "").strip()
        job_url = str(item.get("ref") or "").strip()
        if not title or not job_url:
            return None
        location = item.get("location") or {}
        location_text = ", ".join(
            str(location.get(key) or "").strip() for key in ("city", "region", "country")
            if str(location.get(key) or "").strip()
        )
        job_ad = item.get("jobAd") or {}
        sections = job_ad.get("sections") or {}
        description = "\n".join(
            str((sections.get(key) or {}).get("text") or "")
            for key in ("jobDescription", "qualifications", "additionalInformation")
        ).strip()
        released = _parse_date(item.get("releasedDate"))
        remote = bool(location.get("remote")) or str((item.get("workplaceType") or "")).casefold() == "remote"
        return JobPosting(
            source=self.company, external_id=str(item.get("id") or job_url),
            company=self.company, title=title, location=location_text,
            work_mode="remote" if remote else "onsite_or_hybrid",
            description=description, posted_at=released,
            metadata={
                "company_id": self.company, "country": location.get("country", ""),
                "careers_url": self.careers_url, "application_url": job_url,
                "company_website": self.company_website, "provider": "smartrecruiters",
                "is_startup": False,
            },
        )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
