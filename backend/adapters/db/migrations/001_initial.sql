CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS resumes (
    id BIGSERIAL PRIMARY KEY,
    candidate_name TEXT NOT NULL DEFAULT 'Candidate',
    filename TEXT,
    raw_text TEXT NOT NULL,
    exp_years DOUBLE PRECISION NOT NULL DEFAULT 0,
    skills JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding vector(384),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    company_id TEXT,
    title TEXT NOT NULL,
    location TEXT,
    country TEXT,
    work_mode TEXT,
    description TEXT NOT NULL DEFAULT '',
    req_years DOUBLE PRECISION NOT NULL DEFAULT 0,
    weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    company_website TEXT NOT NULL DEFAULT '',
    careers_url TEXT NOT NULL DEFAULT '',
    application_url TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'direct',
    is_startup BOOLEAN NOT NULL DEFAULT FALSE,
    posted_at TIMESTAMPTZ,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'open',
    content_hash TEXT,
    embedding vector(384),
    enriched_version INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS job_requirements (
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    skill TEXT NOT NULL,
    importance DOUBLE PRECISION NOT NULL DEFAULT 1,
    evidence_text TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(job_id, skill)
);

CREATE TABLE IF NOT EXISTS matches (
    id BIGSERIAL PRIMARY KEY,
    resume_id BIGINT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    config_version TEXT NOT NULL,
    breakdown JSONB NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback (
    id BIGSERIAL PRIMARY KEY,
    resume_id BIGINT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    fetched INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS jobs_embedding_idx
    ON jobs USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
