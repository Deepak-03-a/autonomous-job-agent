"""Ingestion worker boundary.

Concrete company adapters plug into this runner. The runner owns policy,
content hashes, upserts, and ingestion-run telemetry; users read only the DB.
"""

from datetime import datetime
import json
import os
import re
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


def build_relevance(store: PostgresStore):
    """Build a title/skill filter from the newest saved candidate profile."""
    if os.getenv("PROFILE_FILTER", "1").strip().lower() in {"0", "false", "no"}:
        return None
    profile = store.latest_resume_profile()
    skills = {skill.casefold().strip() for skill in profile["skills"] if skill.strip()}
    manual_roles = [role.strip() for role in os.getenv("TARGET_ROLES", "").split(",") if role.strip()]
    roles = manual_roles or profile["target_roles"]
    minimum_hits = max(1, int(os.getenv("PROFILE_MIN_SKILL_HITS", "2")))
    if not skills and not roles:
        return None

    def contains_term(text: str, term: str) -> bool:
        return bool(re.search(r"(?<![\w-])" + re.escape(term.casefold()) + r"(?![\w-])", text.casefold()))

    def relevant(job) -> bool:
        title = str(job.title or "")
        if any(contains_term(title, role) for role in roles):
            return True
        description = f"{title}\n{job.description or ''}"
        hits = sum(1 for skill in skills if contains_term(description, skill))
        return hits >= minimum_hits

    return relevant


def ingest_source(store: PostgresStore, source: JobSource, since: datetime | None = None) -> int:
    jobs = list(source.fetch(since))
    relevance = build_relevance(store)
    if relevance is not None:
        jobs = [job for job in jobs if relevance(job)]
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
