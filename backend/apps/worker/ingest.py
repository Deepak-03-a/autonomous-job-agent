"""Ingestion worker boundary.

Concrete company adapters plug into this runner. The runner owns policy,
content hashes, upserts, and ingestion-run telemetry; users read only the DB.
"""

from datetime import datetime
import json
import os

from adapters.db.postgres import PostgresStore
from adapters.llm.embeddings import MiniLMEmbedder
from adapters.sources.adzuna import AdzunaSource
from adapters.sources.feeds import OfficialFeedSource
from adapters.sources.greenhouse import GreenhouseSource
from adapters.sources.lever import LeverSource
from adapters.sources.protocol import JobSource
from core.config import settings


def ingest_source(store: PostgresStore, source: JobSource, since: datetime | None = None) -> int:
    jobs = source.fetch(since)
    embedder = MiniLMEmbedder(settings.embedding_model, settings.embedding_dimensions)
    for job in jobs:
        embedding = embedder.encode([f"{job.title}\n{job.description}"])[0]
        store.upsert_job(job, embedding)
    return len(jobs)


def configured_sources() -> list[JobSource]:
    raw = os.getenv("COMPANY_SOURCES", "[]")
    entries = json.loads(raw)
    sources: list[JobSource] = []
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
    return sources


def main() -> None:
    store = PostgresStore(settings.database_url)
    for source in configured_sources():
        ingest_source(store, source)


if __name__ == "__main__":
    main()
