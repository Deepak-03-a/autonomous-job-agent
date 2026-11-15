-- One row per agent run: what was asked, which planner produced the plan, every
-- tool step taken, and the report returned. Lets you audit and reopen past runs.
CREATE TABLE IF NOT EXISTS agent_runs (
    id BIGSERIAL PRIMARY KEY,
    goal TEXT NOT NULL DEFAULT '',
    planner TEXT NOT NULL,
    status TEXT NOT NULL,
    trace JSONB NOT NULL DEFAULT '[]'::jsonb,
    report JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_runs_created_idx ON agent_runs(created_at DESC);
