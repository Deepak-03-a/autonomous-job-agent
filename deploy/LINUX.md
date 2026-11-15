# Linux host deployment

The Windows machine is a browser and Remote-SSH development client only. Run
the API, workers, PostgreSQL/pgvector, Ollama, and timers on Linux.

## Environment

Create `/etc/talentalign/talentalign.env`:

```dotenv
DATABASE_URL=postgresql://talentalign:change-me@127.0.0.1:5432/talentalign
TALENTALIGN_API_TOKEN=replace-with-a-long-random-token
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:3b
OLLAMA_KEEP_ALIVE=10m
JSEARCH_API_KEY=
JSEARCH_QUERIES=["software engineer in India","data analyst in India","devops engineer in India","product manager in India"]
ADZUNA_APP_ID=
ADZUNA_API_KEY=
ADZUNA_COUNTRY=in
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
TALENTALIGN_BACKUP_DIR=/var/backups/talentalign
MAX_EXPORT_BYTES=26214400
```

Apply all PostgreSQL migrations from `/opt/talentalign` (the API also applies them on startup):

```bash
cd /opt/talentalign/backend && python -c "from pathlib import Path; from adapters.db.app_store import PostgresApplicationStore; from core.config import settings; store=PostgresApplicationStore(settings.database_url); [store.migrate(p) for p in sorted(Path('adapters/db/migrations').glob('*.sql'))]"
```

Install and enable the units:

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now talentalign-api.service talentalign-enrich.service
sudo systemctl enable --now talentalign-ingest.timer talentalign-backup.timer
```

## Tailscale and firewall

Install Tailscale on both laptops. PostgreSQL and Ollama have no host-published
ports. Docker publishes the API on loopback only; Tailscale Serve proxies it
inside the tailnet:

```bash
sudo tailscale up --hostname=jobbox
sudo tailscale serve --bg 8000
```

Use the Linux MagicDNS name in the Windows browser, for example
`https://jobbox/`. Do not enable Funnel. For a browser on the Linux host,
open `http://127.0.0.1:8000/`.

## Windows client setup

Install Tailscale from the official Windows installer on the Windows laptop,
sign in to the same tailnet account, and verify the Linux peer:

```powershell
tailscale status
tailscale ping jobbox
```

Then open the dashboard at the Linux machine's tailnet name rather than
`127.0.0.1`:

```text
http://jobbox:8000/
```

If Tailscale Serve is enabled on Linux, use its HTTPS URL instead. VS Code
Remote-SSH should target `jobbox` and edit `/opt/talentalign` on Linux; no
Python, PostgreSQL, Ollama, or worker process should run on Windows.

## Ollama

Install Ollama on Linux, pull the configured model, and keep its listener
local-only:

```bash
ollama pull llama3.2:3b
sudo systemctl edit ollama
```

In the systemd override, set conservative CPU-only service limits:

```ini
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=10m"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

Then apply them with `sudo systemctl daemon-reload && sudo systemctl restart
ollama`. `OLLAMA_KEEP_ALIVE` is also passed by the app to generation calls.
The API chat uses Ollama on demand; job requirement extraction runs in the
separate `talentalign-enrich.service`. MiniLM remains responsible for vectors,
so Ollama is not involved in ranking requests. If Ollama is stopped, chat
returns a database-only summary and the enrichment worker retries pending
jobs on its next poll.

## Multi-board sources

The built-in public sources cover Arbeitnow, Remotive, Himalayas, Jobicy, and
Remote OK. Jobicy and Remote OK allow public feed access and require source
credit and links to their listing pages. To add listings aggregated from
LinkedIn, Indeed, Glassdoor, Monster, and other boards, create a JSearch
RapidAPI subscription and set `JSEARCH_API_KEY`. Configure `JSEARCH_QUERIES`
to choose the role families ingested every six hours. An Adzuna application ID
and key can also enable that source. Credentials belong in the server
environment; Ollama analyzes/ranks ingested listings but does not fetch jobs.

LinkedIn and Indeed access is subject to their partner/API terms. Their
official APIs do not provide an unrestricted public job-search endpoint for
this app, so those publishers are available here through JSearch only when its
key and subscription permit them.

When `TALENTALIGN_API_TOKEN` is set, opening the dashboard issues a random,
same-site HttpOnly session cookie for its API requests. PostgreSQL stores only
the session token hash, so the shared API token stays in the server `.env` and
does not need to be copied into browser storage. Sessions expire after 30 days.

## Import your LinkedIn data

TalentAlign does not log into or scrape LinkedIn. Download your own account
archive from LinkedIn's **Settings → Data privacy → Get a copy of your data**.
The importer reads only profile, positions, skills, education, certifications,
saved jobs, applications, alerts, and job-seeker preference CSVs. It processes
the zip in memory and skips files over 5 MB; the upload limit defaults to 25 MB.

You can upload the archive through the authenticated API and optionally merge it
with an existing resume profile:

```bash
curl -H "Authorization: Bearer $TALENTALIGN_API_TOKEN" \
     -F "file=@Basic_LinkedInDataExport.zip" \
     https://jobbox.<your-tailnet>.ts.net/api/v1/profile/linkedin-export
```

The response includes imported positions, combined skills, experience estimate,
predicted role domains, and target search roles. Alert phrases from the archive
are prioritized. `TARGET_ROLES` in the server environment overrides predicted
roles for ingestion. Imported profile details are stored with the resume record;
the original zip is not retained.

## Run the job-search agent

The agent reads the latest saved resume or LinkedIn profile, checks which
providers supplied stored jobs, ranks matches, identifies high impact skill
gaps, and returns a report with an execution trace.

```bash
# Fixed pipeline; does not ask Ollama to plan
curl -H "Authorization: Bearer $TALENTALIGN_API_TOKEN" -X POST \
     https://jobbox.<your-tailnet>.ts.net/api/v1/agent/run

# A natural-language goal; Ollama selects from validated tools
curl -H "Authorization: Bearer $TALENTALIGN_API_TOKEN" \
     -F "goal=remote jobs that need Terraform" \
     https://jobbox.<your-tailnet>.ts.net/api/v1/agent/run
```

Set `planner=fixed` to skip planning, `narrative=off` to skip the model-written
summary, or `resume_id=N` to select a saved profile. PostgreSQL deployments
retain runs at `GET /api/v1/agent/runs` and `GET /api/v1/agent/runs/{id}`.
Ollama can choose only validated tools and filters; job records and scores come
from the database and deterministic ranking code. Generated summaries are
shown only when their evidence and numbers are grounded in report data. If
Ollama is unavailable or returns an invalid plan, the fixed pipeline runs.
