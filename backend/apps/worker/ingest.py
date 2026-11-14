"""Ingestion worker boundary.

Concrete company adapters plug into this runner. The runner owns policy,
content hashes, upserts, and ingestion-run telemetry; users read only the DB.
"""

from datetime import datetime
import json
import os
import time

from adapters.db.postgres import PostgresStore
from adapters.llm.embeddings import MiniLMEmbedder
from adapters.sources.adzuna import AdzunaSource
from adapters.sources.feeds import OfficialFeedSource
from adapters.sources.greenhouse import GreenhouseSource
from adapters.sources.lever import LeverSource
from adapters.sources.public_market import PublicMarketSource
from adapters.sources.jsearch import JSearchSource
from adapters.sources.protocol import JobSource
from core.config import settings


def ingest_source(store: PostgresStore, source: JobSource, since: datetime | None = None) -> int:
    jobs = list(source.fetch(since))
    embedder = MiniLMEmbedder(settings.embedding_model, settings.embedding_dimensions)
    embeddings = embedder.encode([f"{job.title}\n{job.description}" for job in jobs]) if jobs else []
    for job, embedding in zip(jobs, embeddings):
        store.upsert_job(job, embedding)
    return len(jobs)


def configured_sources() -> list[JobSource]:
    raw = os.getenv("COMPANY_SOURCES", "[]")
    entries = json.loads(raw)
    if not entries:
        sources: list[JobSource] = [
            PublicMarketSource(
                country=os.getenv("INGEST_COUNTRY", "India"),
                max_pages=int(os.getenv("INGEST_MAX_PAGES", "2")),
            )
        ]
        if os.getenv("JSEARCH_API_KEY"):
            sources.append(JSearchSource(country=os.getenv("INGEST_COUNTRY", "India")))
        if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_API_KEY"):
            sources.append(AdzunaSource(country=os.getenv("ADZUNA_COUNTRY", "in")))
        return sources
    sources = []
    for entry in entries:
        provider = entry["provider"]
        if provider == "greenhouse":
            sources.append(GreenhouseSource(entry["board"], entry["name"], entry["careers_url"], entry["company_website"]))
        elif provider == "lever":
            sources.append(LeverSource(entry["board"], entry["name"], entry["careers_url"], entry["company_website"]))
        elif provider == "feed":
            sources.append(OfficialFeedSource(entry["name"], entry["feed_url"], entry["careers_url"], entry["company_website"]))
        elif provider == "adzuna":
            sources.append(AdzunaSource(entry.get("country", "in")))
        elif provider == "jsearch":
            sources.append(JSearchSource(entry.get("country", "India"), entry.get("queries")))
    providers = {entry.get("provider") for entry in entries}
    if os.getenv("JSEARCH_API_KEY") and "jsearch" not in providers:
        sources.append(JSearchSource(country=os.getenv("INGEST_COUNTRY", "India")))
    if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_API_KEY") and "adzuna" not in providers:
        sources.append(AdzunaSource(country=os.getenv("ADZUNA_COUNTRY", "in")))
    return sources


def main() -> None:
    store = PostgresStore(settings.database_url)
    interval = int(os.getenv("INGEST_INTERVAL_SECONDS", "21600"))
    sources = configured_sources()
    while True:
        for source in sources:
            try:
                count = ingest_source(store, source)
                print(f"Ingested {count} listings from {source.policy.name}", flush=True)
            except Exception as error:
                print(f"Ingestion failed for {source.policy.name}: {error}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
