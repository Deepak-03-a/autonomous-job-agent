"""Enrichment worker entrypoint; run this on Linux under systemd."""

import time

from adapters.db.postgres import PostgresStore
from adapters.llm.ollama import OllamaClient
from core.config import settings


def run_once(store: PostgresStore, ollama: OllamaClient, limit: int = 25) -> int:
    jobs = store.claim_enrichment_jobs(limit)
    for job in jobs:
        description = job.get("description") or ""
        schema = {
            "type": "object",
            "properties": {
                "requirements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "skill": {"type": "string"},
                            "importance": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence_text": {"type": "string"},
                        },
                        "required": ["skill", "importance", "evidence_text"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["requirements"],
            "additionalProperties": False,
        }
        prompt = (
            "Extract skills only when the job description explicitly names them. "
            "For each skill provide a short verbatim evidence_text copied from the "
            "description. importance is 0 to 1. Return an empty array if none.\n\n"
            f"Title: {job['title']}\nDescription: {description}"
        )
        try:
            result = ollama.generate_json(prompt, schema)
            requirements = result.get("requirements", [])
            if not isinstance(requirements, list):
                raise ValueError("requirements must be an array")
            # Treat model output as untrusted: discard malformed or unsupported
            # items before writing them to the database.
            verified = []
            for item in requirements:
                if not isinstance(item, dict):
                    continue
                skill = item.get("skill")
                evidence = item.get("evidence_text")
                if not isinstance(skill, str) or not skill.strip():
                    continue
                if not isinstance(evidence, str) or not evidence.strip() or evidence.casefold() not in description.casefold():
                    continue
                try:
                    importance = min(1.0, max(0.0, float(item.get("importance", 0.5))))
                except (TypeError, ValueError):
                    importance = 0.5
                verified.append({"skill": skill.strip(), "importance": importance, "evidence_text": evidence.strip()})
            store.mark_enriched(job["id"], verified)
        except Exception as error:
            # Leave the row pending so the next poll retries after a transient
            # Ollama or database failure instead of killing the worker service.
            store.release_enrichment_job(job["id"])
            print(f"Enrichment failed for job {job.get('id')}: {error}", flush=True)
    return len(jobs)


def main() -> None:
    store = PostgresStore(settings.database_url)
    ollama = OllamaClient(settings.ollama_url, settings.ollama_model, keep_alive=settings.ollama_keep_alive)
    while True:
        run_once(store, ollama)
        time.sleep(settings.queue_poll_seconds)


if __name__ == "__main__":
    main()
