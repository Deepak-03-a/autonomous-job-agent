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
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
TALENTALIGN_BACKUP_DIR=/var/backups/talentalign
```

Run the PostgreSQL migration from `/opt/talentalign/backend`:

```bash
python -c "from adapters.db.postgres import PostgresStore; from core.config import settings; PostgresStore(settings.database_url).migrate('adapters/db/migrations/001_initial.sql')"
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

When `TALENTALIGN_API_TOKEN` is set, opening the dashboard issues a random,
same-site HttpOnly session cookie for its API requests. PostgreSQL stores only
the session token hash, so the shared API token stays in the server `.env` and
does not need to be copied into browser storage. Sessions expire after 30 days.
