# TalentAlign AI

TalentAlign imports a resume or a LinkedIn data archive, ingests job listings
from configured public feeds and ATS APIs, ranks stored listings against the
candidate profile, and can run a tool-using search agent with Ollama.

## Run with Docker Compose

Create a local `.env` from the example and set a long random API token before
starting the stack:

```bash
cp deploy/talentalign.env.example .env
openssl rand -hex 32
docker compose up -d --build
```

Paste the generated value into `TALENTALIGN_API_TOKEN` in `.env`. Production
Compose requires this token and PostgreSQL. PostgreSQL is the production path;
SQLite is available only when explicitly setting `APP_ENV=development` or `test`.
The API is bound to `127.0.0.1:8000` by default; use the documented
Tailscale/reverse-proxy setup for remote access.

## Job sources

The built-in public feeds include Arbeitnow, Remotive, Himalayas, Jobicy, and
Remote OK. Configure company ATS boards in `COMPANY_SOURCES` using `greenhouse`,
`lever`, `ashby`, or `smartrecruiters`, with each provider's public board key.
Optional JSearch and Adzuna adapters require their own credentials. JSearch may
return listings attributed to LinkedIn, Indeed, or other publishers; TalentAlign
calls the aggregator API and does not log in to or directly scrape those boards.
Manual company-page fetches validate public destinations and honor `robots.txt`.

## Profile and agent

Upload a PDF resume from the dashboard. To import LinkedIn data, request your
archive through LinkedIn's account settings and upload the ZIP through the
profile import endpoint. The app does not ask for LinkedIn credentials.

The dashboard's **Job agent** panel calls `POST /api/v1/agent/run`. With no goal,
it uses a fixed plan; with a natural-language goal, Ollama may choose from a
validated set of profile, freshness, ranking, skill-gap, and job-explanation
tools. Matching and scoring are deterministic application code. Ollama can
summarize only when its evidence and numbers are grounded in the report data.
PostgreSQL retains run traces at `GET /api/v1/agent/runs`.

## Local frontend development

The shipped UI is served from the API origin. If you use a separate dev server,
set `CORS_ORIGINS` to its exact origin, for example
`http://localhost:5173,http://127.0.0.1:5173`; wildcard origins are not enabled.

See [deploy/LINUX.md](deploy/LINUX.md) for host setup, backups, environment
variables, and API examples.
