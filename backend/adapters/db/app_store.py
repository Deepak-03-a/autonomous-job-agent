"""Application-facing PostgreSQL store used when DATABASE_URL is PostgreSQL."""

import json
from pathlib import Path
from typing import Any

from core.domain import JobPosting


class PostgresApplicationStore:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _connect(self):
        import psycopg
        from pgvector.psycopg import register_vector
        from psycopg.rows import dict_row

        connection = psycopg.connect(self.database_url, row_factory=dict_row)
        register_vector(connection)
        return connection

    def migrate(self, migration_path: str | Path) -> None:
        sql = Path(migration_path).read_text(encoding="utf-8")
        with self._connect() as connection:
            for statement in sql.split(";\n"):
                statement = statement.strip()
                if statement:
                    connection.execute(statement)

    def get_resume(self, resume_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT id, raw_text, exp_years, skills FROM resumes WHERE id = %s",
                (resume_id,),
            ).fetchone()

    def latest_resume(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT id, raw_text, exp_years, skills FROM resumes ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def insert_resume(self, filename: str, parsed: dict[str, Any], user_id: int = 1) -> int:
        del user_id  # PostgreSQL schema uses candidate profiles; auth is API-level.
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO resumes(filename, raw_text, exp_years, skills)
                VALUES (%s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    filename,
                    parsed["raw_text"],
                    parsed["exp_years"],
                    json.dumps(parsed["skills"]),
                ),
            ).fetchone()
            return int(row["id"])

    def upsert_jobs(self, jobs: list[dict[str, Any]], country: str) -> list[int]:
        ids: list[int] = []
        with self._connect() as connection:
            for job in jobs:
                row = connection.execute(
                    """
                    INSERT INTO jobs(
                        source, external_id, company_id, title, location, country, work_mode,
                        description, req_years, weights, company_website, careers_url,
                        application_url, provider, is_startup, posted_at, status, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, 'open', %s)
                    ON CONFLICT(source, external_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        location = EXCLUDED.location,
                        country = EXCLUDED.country,
                        description = EXCLUDED.description,
                        req_years = EXCLUDED.req_years,
                        weights = EXCLUDED.weights,
                        company_website = EXCLUDED.company_website,
                        careers_url = EXCLUDED.careers_url,
                        application_url = EXCLUDED.application_url,
                        provider = EXCLUDED.provider,
                        is_startup = EXCLUDED.is_startup,
                        posted_at = EXCLUDED.posted_at,
                        last_seen = now(),
                        status = 'open',
                        content_hash = EXCLUDED.content_hash
                    RETURNING id
                    """,
                    (
                        job.get("source", "official-company"),
                        job["job_code"],
                        job["company"],
                        job["title"],
                        job.get("location", ""),
                        job.get("country", country),
                        "remote" if job.get("remote") else "onsite_or_hybrid",
                        job.get("description", ""),
                        job.get("req_years", 0.0),
                        json.dumps(job.get("weights", {})),
                        job.get("company_website", ""),
                        job.get("careers_url") or job.get("portal_url", ""),
                        job.get("application_url", ""),
                        job.get("provider", "direct"),
                        bool(job.get("is_startup", False)),
                        job.get("posted_at") or None,
                        job.get("job_code", ""),
                    ),
                ).fetchone()
                if row:
                    ids.append(int(row["id"]))
        return ids

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id, external_id, title, company_id AS company, careers_url AS url,
                          description, req_years, weights, location, country,
                          provider AS source, company_website, careers_url,
                          application_url, provider, is_startup,
                          posted_at
                   FROM jobs WHERE id = %s AND careers_url <> ''""",
                (job_id,),
            ).fetchone()
            return self._format_job(row)

    def latest_job(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id, external_id, title, company_id AS company, careers_url AS url,
                          description, req_years, weights, location, country,
                          provider AS source, company_website, careers_url,
                          application_url, provider, is_startup,
                          posted_at
                   FROM jobs WHERE careers_url <> '' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            return self._format_job(row)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, external_id, title, company_id AS company, careers_url AS url,
                          description, req_years, weights, location, country,
                          provider AS source, company_website, careers_url,
                          application_url, provider, is_startup,
                          posted_at
                   FROM jobs WHERE careers_url <> '' ORDER BY id ASC"""
            ).fetchall()
            return [self._format_job(row) for row in rows]

    def search_jobs_by_embedding(
        self,
        embedding: list[float],
        country: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, external_id, title, company_id AS company, careers_url AS url,
                       description, req_years, weights, location, country,
                       provider AS source, company_website, careers_url,
                       application_url, provider, is_startup, posted_at
                FROM jobs
                WHERE status = 'open' AND careers_url <> ''
                  AND (%s = '' OR location ILIKE %s OR country ILIKE %s OR work_mode = 'remote')
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (country, f"%{country}%", f"%{country}%", embedding, limit),
            ).fetchall()
            return [self._format_job(row) for row in rows]

    def insert_match(self, resume_id: int, job: dict[str, Any], evaluation: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO matches(resume_id, job_id, config_version, breakdown, score)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                """,
                (
                    resume_id,
                    job["id"],
                    "1",
                    json.dumps(evaluation),
                    evaluation["final_match_score_pct"] / 100.0,
                ),
            )

    @staticmethod
    def _format_job(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        weights = row.get("weights") or {}
        return {
            **row,
            "job_code": row.get("external_id") or str(row["id"]),
            "portal_url": row.get("url") or "",
            "remote": row.get("work_mode") == "remote",
            "weights": weights,
            "weights_json": json.dumps(weights),
            "req_years": row.get("req_years") or 0.0,
            "location": row.get("location") or "",
            "country": row.get("country") or "",
            "source": row.get("source") or "",
            "company_website": row.get("company_website") or "",
            "careers_url": row.get("careers_url") or "",
            "application_url": row.get("application_url") or "",
            "posted_at": row.get("posted_at") or "",
        }
