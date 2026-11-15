-- Profile details that are not part of scoring: predicted domains, target roles,
-- LinkedIn saved jobs / applications / alert phrases, skills outside the taxonomy.
ALTER TABLE resumes ADD COLUMN IF NOT EXISTS extras JSONB NOT NULL DEFAULT '{}'::jsonb;
