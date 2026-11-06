"""RSS/Atom adapter for official company career feeds."""

from datetime import datetime

import feedparser

from adapters.sources.protocol import SourcePolicy
from core.domain import JobPosting


class OfficialFeedSource:
    def __init__(self, name: str, feed_url: str, careers_url: str, company_website: str):
        self.name = name
        self.feed_url = feed_url
        self.careers_url = careers_url
        self.company_website = company_website
        self.policy = SourcePolicy(name, 30, 4, careers_url, check_robots=True)

    def fetch(self, since: datetime | None = None) -> list[JobPosting]:
        feed = feedparser.parse(self.feed_url)
        results = []
        for item in feed.entries:
            published = item.get("published_parsed")
            posted_at = datetime(*published[:6]) if published else None
            results.append(JobPosting(
                source=self.name,
                external_id=item.get("id") or item.get("link", ""),
                company=self.name,
                title=item.get("title", ""),
                description=item.get("summary", ""),
                posted_at=posted_at,
                metadata={
                    "company_website": self.company_website,
                    "careers_url": self.careers_url,
                    "application_url": item.get("link", ""),
                    "provider": "feed",
                },
            ))
        return results
