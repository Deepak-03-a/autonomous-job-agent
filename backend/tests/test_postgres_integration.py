"""PostgreSQL/pgvector integration coverage (set TEST_DATABASE_URL to enable)."""

import os
from pathlib import Path
from uuid import uuid4

import pytest

from adapters.db.app_store import PostgresApplicationStore
from adapters.db.postgres import PostgresStore
from core.domain import JobPosting


@pytest.fixture
def postgres_schema():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable pgvector database.")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    schema = f"talentalign_test_{uuid4().hex}"
    with psycopg.connect(database_url) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated_url = make_conninfo(database_url, options=f"-c search_path={schema},public")
    migrations = Path(__file__).parents[1] / "adapters" / "db" / "migrations"
    try:
        store = PostgresStore(isolated_url)
        for migration in sorted(migrations.glob("*.sql")):
            store.migrate(migration)
        yield isolated_url
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_vector_search_filters_before_ordering_and_updates_work_mode(postgres_schema):
    queue = PostgresStore(postgres_schema)
    app_store = PostgresApplicationStore(postgres_schema)
    vector_a = [1.0] + [0.0] * 383
    vector_b = [0.0, 1.0] + [0.0] * 382

    def upsert(external_id, title, work_mode, weights, vector):
        queue.upsert_job(JobPosting(
            source="test", external_id=external_id, company="Example Corp", title=title,
            location="India", work_mode=work_mode, description=title,
            requirements=weights,
            metadata={"company_id": "Example Corp", "country": "India", "weights": weights,
                      "careers_url": "https://example.test/jobs", "application_url": "https://example.test/apply",
                      "provider": "test"},
        ), vector)

    upsert("remote", "Remote DevOps", "remote", {"terraform": 1.0}, vector_a)
    upsert("office", "Office DevOps", "onsite_or_hybrid", {"terraform": 1.0}, vector_b)
    results = app_store.search_jobs_by_embedding(
        vector_b, limit=10, filters={"remote_only": True, "must_have_skill": "Terraform"},
    )
    assert [row["external_id"] for row in results] == ["remote"]
    assert results[0]["work_mode"] == "remote"

    upsert("remote", "Remote DevOps", "onsite_or_hybrid", {"terraform": 1.0}, vector_a)
    results = app_store.search_jobs_by_embedding(vector_a, limit=10, filters={"remote_only": True})
    assert results == []


def test_agent_run_migration_is_applied(postgres_schema):
    with PostgresStore(postgres_schema).connection() as connection:
        row = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'agent_runs') AS present"
        ).fetchone()
    assert row["present"]
