"""JSearch adapter for licensed multi-board job discovery.

JSearch returns listings aggregated from public job sites and includes the
publisher for each listing. It requires the account owner's RapidAPI key.
"""

import hashlib
import json
import os
from datetime import datetime

import httpx

from adapters.sources.protocol import SourcePolicy
from core.domain import JobPosting


class JSearchSource:
    endpoint = "https://jsearch.p.rapidapi.com/search"

    def __init__(self, country: str = "India", queries: list[str] | None = None):
        self.country = country
        self.api_key = os.environ["JSEARCH_API_KEY"]
        self.queries = queries or _queries_from_environment(country)
        self.policy = SourcePolicy(
            "JSearch multi-board", min_interval_s=900, max_runs_per_day=4,
            attribution="Original job-board publisher", check_robots=False,
        )

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        del since
        postings: list[JobPosting] = []
        seen_ids: set[str] = set()
        headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        with httpx.Client(timeout=30, headers=headers) as client:
            for query in self.queries:
                response = client.get(
                    self.endpoint,
                    params={
                        "query": query,
                        "page": "1",
                        "num_pages": "1",
                        "date_posted": "all",
                        "country": "in" if self.country.casefold() == "india" else self.country.casefold(),
                    },
                )
                response.raise_for_status()
                for item in response.json().get("data", []):
                    posting = self._to_posting(item)
                    if posting and posting.external_id not in seen_ids:
                        seen_ids.add(posting.external_id)
                        postings.append(posting)
        return postings

    def _to_posting(self, item: dict) -> JobPosting | None:
        title = str(item.get("job_title") or "").strip()
        company = str(item.get("employer_name") or "").strip()
        application_url = str(item.get("job_apply_link") or "").strip()
        if not title or not company or not application_url:
            return None
        publisher = str(item.get("job_publisher") or "JSearch").strip()
        external_id = str(item.get("job_id") or hashlib.sha1(application_url.encode("utf-8")).hexdigest())
        location = ", ".join(
            value for value in (
                str(item.get("job_city") or "").strip(),
                str(item.get("job_state") or "").strip(),
                str(item.get("job_country") or "").strip(),
            ) if value
        )
        description = str(item.get("job_description") or "")
        posted_at = _parse_timestamp(item.get("job_posted_at_datetime_utc"))
        remote = bool(item.get("job_is_remote"))
        content_hash = hashlib.sha256(
            f"{title}\n{company}\n{location}\n{description}".encode("utf-8")
        ).hexdigest()
        return JobPosting(
            source=f"JSearch:{publisher}",
            external_id=external_id,
            company=company,
            title=title,
            location=location,
            work_mode="remote" if remote else "onsite_or_hybrid",
            description=description,
            posted_at=posted_at,
            content_hash=content_hash,
            metadata={
                "country": str(item.get("job_country") or self.country),
                "company_id": company,
                "careers_url": application_url,
                "application_url": application_url,
                "provider": publisher,
                "is_startup": False,
            },
        )


def _queries_from_environment(country: str) -> list[str]:
    raw = os.getenv("JSEARCH_QUERIES", "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                return [item.strip() for item in parsed if item.strip()]
        except json.JSONDecodeError:
            pass
    return [
        f"software engineer in {country}",
        f"data analyst in {country}",
        f"devops engineer in {country}",
        f"product manager in {country}",
    ]


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
