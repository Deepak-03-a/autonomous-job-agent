"""Adzuna adapter for broad market discovery.

Adzuna is an ingestion source only. Its redirect URL is never exposed as the
user's application destination; a company careers URL must be supplied by the
company catalog before a result is shown as an official destination.
"""

import os
from datetime import datetime

import httpx

from adapters.sources.protocol import SourcePolicy
from core.domain import JobPosting


class AdzunaSource:
    def __init__(self, country: str = "in"):
        self.country = country
        self.app_id = os.environ["ADZUNA_APP_ID"]
        self.api_key = os.environ["ADZUNA_API_KEY"]
        self.policy = SourcePolicy("Adzuna", 30, 4, "https://www.adzuna.in/", check_robots=True)

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        del since
        response = httpx.get(
            f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1",
            params={"app_id": self.app_id, "app_key": self.api_key, "results_per_page": 50},
            timeout=20,
        )
        response.raise_for_status()
        return [
            JobPosting(
                source="Adzuna",
                external_id=str(item["id"]),
                company=(item.get("company") or {}).get("display_name", ""),
                title=item.get("title", ""),
                location=(item.get("location") or {}).get("display_name", ""),
                description=item.get("description", ""),
                posted_at=datetime.fromisoformat(item["created"].replace("Z", "+00:00")) if item.get("created") else None,
                metadata={"aggregator_url": item.get("redirect_url", ""), "provider": "adzuna"},
            )
            for item in response.json().get("results", [])
        ]
