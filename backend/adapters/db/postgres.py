"""Small PostgreSQL repository/queue adapter.

The module imports psycopg lazily so the existing SQLite development workflow
continues to work on Windows. Linux deployment sets DATABASE_URL and installs
the PostgreSQL extras from requirements.txt.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.domain import JobPosting


class PostgresStore:
    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def connection(self):
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            from pgvector.psycopg import register_vector

            register_vector(connection)
            yield connection

    def migrate(self, migration_path: str | Path) -> None:
        migration_sql = Path(migration_path).read_text(encoding="utf-8")
        with self.connection() as connection:
            for statement in migration_sql.split(";\n"):
                statement = statement.strip()
                if statement:
                    connection.execute(statement)

    def upsert_job(self, job: JobPosting, embedding: list[float] | None = None) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs
                    (source, external_id, company_id, title, location, work_mode,
                     description, posted_at, first_seen, last_seen, status,
                     content_hash, embedding, enriched_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now(), %s, %s, %s, 0)
                ON CONFLICT (source, external_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    location = EXCLUDED.location,
                    work_mode = EXCLUDED.work_mode,
                    description = EXCLUDED.description,
                    posted_at = EXCLUDED.posted_at,
                    last_seen = now(),
                    status = EXCLUDED.status,
                    content_hash = EXCLUDED.content_hash,
                    embedding = COALESCE(EXCLUDED.embedding, jobs.embedding),
                    enriched_version = CASE
                        WHEN jobs.content_hash IS DISTINCT FROM EXCLUDED.content_hash THEN 0
                        ELSE jobs.enriched_version
                    END
                """,
                (
                    job.source,
                    job.external_id,
                    job.metadata.get("company_id"),
                    job.title,
                    job.location,
                    job.work_mode,
                    job.description,
                    job.posted_at,
                    job.status,
                    job.content_hash,
                    embedding,
                ),
            )

    def claim_enrichment_jobs(self, limit: int = 25) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, source, external_id, title, description
                FROM jobs
                WHERE status = 'open' AND enriched_version < 1
                ORDER BY last_seen
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return list(rows)

    def search_similar_jobs(
        self,
        embedding: list[float],
        country: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve candidates with pgvector before pure scoring/gates."""
        where = "status = 'open'"
        params: list[Any] = []
        if country:
            where += " AND (location ILIKE %s OR country ILIKE %s OR work_mode ILIKE 'remote')"
            params.extend([f"%{country}%", f"%{country}%"])
        params.extend([embedding, limit])
        with self.connection() as connection:
            return list(connection.execute(
                f"""
                SELECT id, source, external_id, company_id, title, location,
                       work_mode, description, posted_at, status
                FROM jobs
                WHERE {where}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                params,
            ).fetchall())

    def mark_enriched(self, job_id: int, requirements: list[dict[str, Any]]) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM job_requirements WHERE job_id = %s", (job_id,))
            for requirement in requirements:
                connection.execute(
                    """
                    INSERT INTO job_requirements(job_id, skill, importance, evidence_text)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        requirement["skill"],
                        requirement.get("importance", 1.0),
                        requirement.get("evidence_text", ""),
                    ),
                )
            connection.execute(
                "UPDATE jobs SET enriched_version = enriched_version + 1 WHERE id = %s",
                (job_id,),
            )
