"""Public job boards used to seed and refresh the PostgreSQL market index."""

import hashlib
import os
from datetime import datetime

from adapters.sources.protocol import SourcePolicy
from core.domain import JobPosting


class PublicMarketSource:
    """Ingest public job-board listings without requiring per-user searches."""

    def __init__(self, country: str = "India", max_pages: int = 2):
        self.country = country
        self.max_pages = max_pages
        self.policy = SourcePolicy(
            "Public job boards", min_interval_s=900, max_runs_per_day=4,
            attribution="Original job posting source", check_robots=True,
        )

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        del since
        # The API and ingestion worker share the same public source normalizer,
        # but ingestion runs independently of a candidate's search request.
        from app.agents.crawler import MarketDiscoveryAgent

        agent = MarketDiscoveryAgent()
        jobs = agent.discover_market_jobs(
            candidate_skills=[],
            candidate_years=0,
            country=self.country,
            max_pages=self.max_pages,
        )
        postings = []
        for job in jobs:
            description = job.get("description") or ""
            posted_at = job.get("posted_at") or None
            if isinstance(posted_at, str):
                try:
                    posted_at = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                except ValueError:
                    posted_at = None
            company = job.get("company") or "Unknown company"
            title = job.get("title") or "Untitled role"
            external_id = job.get("job_code") or hashlib.sha1(
                f"{job.get('source')}|{job.get('portal_url')}".encode("utf-8")
            ).hexdigest()
            content_hash = hashlib.sha256(
                f"{title}\n{company}\n{job.get('location', '')}\n{description}".encode("utf-8")
            ).hexdigest()
            postings.append(JobPosting(
                source=job.get("source") or "Public job board",
                external_id=str(external_id),
                company=company,
                title=title,
                location=job.get("location") or "",
                work_mode="remote" if job.get("remote") else "onsite_or_hybrid",
                description=description,
                posted_at=posted_at,
                content_hash=content_hash,
                requirements=job.get("weights") or {},
                metadata={
                    "country": job.get("country") or self.country,
                    "company_id": company,
                    "req_years": job.get("req_years") or 0,
                    "weights": job.get("weights") or {},
                    "careers_url": job.get("portal_url") or "",
                    "application_url": job.get("portal_url") or "",
                    "provider": job.get("source") or "Public job board",
                    "is_startup": bool(job.get("is_startup")),
                },
            ))
        print(
            f"Public job boards returned {len(postings)} listings for {self.country}; "
            f"source errors: {len(agent.last_errors)}",
            flush=True,
        )
        return postings
