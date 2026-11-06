"""Enrichment worker entrypoint; run this on Linux under systemd."""

import time

from adapters.db.postgres import PostgresStore
from adapters.llm.ollama import OllamaClient
from core.config import settings


def run_once(store: PostgresStore, ollama: OllamaClient, limit: int = 25) -> int:
    jobs = store.claim_enrichment_jobs(limit)
    for job in jobs:
        result = ollama.generate_json(
            "Extract only skills explicitly supported by this job description. "
            f"Return JSON with requirements, where requirements is an array of objects with skill, importance, evidence_text.\n\n"
            f"Title: {job['title']}\nDescription: {job['description']}",
            {"type": "object", "properties": {"requirements": {"type": "array"}}},
        )
        store.mark_enriched(job["id"], result.get("requirements", []))
    return len(jobs)


def main() -> None:
    store = PostgresStore(settings.database_url)
    ollama = OllamaClient(settings.ollama_url, settings.ollama_model)
    while True:
        run_once(store, ollama)
        time.sleep(settings.queue_poll_seconds)


if __name__ == "__main__":
    main()
