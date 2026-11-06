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

Install Tailscale on both laptops. Keep PostgreSQL and Ollama bound to
`127.0.0.1`. Allow only the API over the tailnet:

```bash
sudo tailscale up --hostname=jobbox
sudo ufw allow in on tailscale0 to any port 8000 proto tcp
sudo tailscale serve --bg 8000
```

Use the Linux MagicDNS name in the Windows browser, for example
`https://jobbox/` or `http://jobbox:8000/`, depending on the Tailscale Serve
mode configured on the Linux machine. Do not enable Funnel.

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
