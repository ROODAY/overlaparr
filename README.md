# Overlaparr

Overlaparr finds media in your Sonarr and Radarr libraries that is already available on
other Plex servers you can access, shows how each overlap differs (quality, resolution,
which server has it), and lets you decide what to do about it.

It is **report-first**: a scan only reads from Plex, Sonarr and Radarr. Nothing in your
libraries changes until you act on a finding yourself, or explicitly opt an automation rule in.

## Quick start (Docker Compose)

1. Copy the example compose file and edit it:

   ```sh
   cp docker-compose.example.yml docker-compose.yml
   ```

   Set `PUID`/`PGID` to the user that should own the config files and `TZ` to your time zone.
   The Sonarr/Radarr services in the example are only examples; remove them and point
   Overlaparr at your existing containers if you already run them.

2. Start it:

   ```sh
   docker compose up -d
   ```

3. Open `http://<host>:8686` and follow the first-run wizard:
   1. Create the admin account.
   2. Sign in with Plex, or paste a Plex token.
   3. Choose your home server (the one your Sonarr/Radarr feed).
   4. Add your Sonarr/Radarr instances (e.g. `http://sonarr:8989`, `http://radarr:7878`).
   5. Run the first scan.

## Configuration

Everything lives in the `/config` volume:

| Path | Contents |
| --- | --- |
| `config.yaml` | All settings, rules and notification connections. |
| `secrets.yaml` | Tokens, API keys and passwords (created with mode 600). |
| `overlaparr.db` | SQLite runtime state: findings, activity, scan history. |
| `logs/overlaparr.log` | JSON application log (rotated). |

`config.yaml` is the source of truth and is safe to commit to version control
(secrets are never written into it: webhook header values included).
**Never commit `secrets.yaml`.** If you keep `/config` in git, ignore the items that must
never be committed:

```gitignore
secrets.yaml
*.db
*.db-wal
*.db-shm
logs/
```

- Secrets are referenced from `config.yaml` as `!secret <key>`, pointing at a key in
  `secrets.yaml`.
- Any value may be written as `${ENV_VAR}`; the environment value wins when it is set.
- Hand edits to either file are picked up automatically (hot reload). If an edit makes the
  file invalid, Overlaparr keeps running on the last good configuration and shows a banner
  explaining the error until you fix it.
- You can download the current `config.yaml` from **Settings**.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUID` | `1000` | User id the app runs as and that owns `/config`. |
| `PGID` | `1000` | Group id for the same. |
| `TZ` | `UTC` | Time zone for the scan, digest and housekeeping schedules (log timestamps are always UTC). |
| `OVERLAPARR_PORT` | `8686` | Port the app listens on **inside the container** (the healthcheck follows it). |

The example compose file passes `OVERLAPARR_PORT` into the container and maps the same port
on the host (`${OVERLAPARR_PORT:-8686}:${OVERLAPARR_PORT:-8686}`), so setting it in your shell
or a `.env` file changes both. With plain `docker run`, set it with `-e` and map that port.

## Safety

- **Report-only by default.** Out of the box a scan only reads. The optional features that
  write to Sonarr/Radarr are all off until you turn them on: tag mirroring, per-instance
  loss handling (re-monitor/search when an item disappears elsewhere) and automation rules.
- **Deletes are permanent.** A delete either removes the files (the movie or
  episodes stay in Sonarr/Radarr, unmonitored) or removes the whole movie/series with its
  files. The delete
  dialog says "Deleted files cannot be undone" before you confirm.
- **Automation is opt-in per rule.** Rules are created disabled, and enabling one shows a
  warning describing what it will do. Enabling a delete rule also requires typing `DELETE`.
- **Dry run.** "Dry run" on a rule makes the next scan log what the rule would do without
  doing it.
- **Offline or stale servers never count as "gone".** If a Plex server can't be reached, its
  copies are kept as stale rather than marked lost, so nothing acts on that assumption.
- Overlaparr never removes the ignore tag (`dupe-ignore` by default). Add it to an item in
  Sonarr/Radarr to exclude that item from findings and automation.

## Authentication

Overlaparr has a single user with a forms login. For scripting, send the API key in the
`X-Api-Key` header; it is generated on first start as `app_api_key` in `secrets.yaml`.

Two endpoints manage those credentials (both need authentication):

- `POST /api/v1/auth/password` with `{"current_password": "...", "new_password": "..."}`
  changes the password (at least 8 characters / at most 72 bytes). Every other signed-in
  session is invalidated; the caller stays signed in. Only a session cookie may call it: the
  API key and the local bypass get 403, so a leaked API key can't lock you out.
- `POST /api/v1/auth/api-key/regenerate` returns `{"api_key": "..."}` with a fresh key,
  shown only in that response and stored in `secrets.yaml`. The old key stops working at
  once. No endpoint ever returns the current key.

Browser cross-site writes are rejected: a `POST`/`PUT`/`PATCH`/`DELETE` to `/api/*` gets 403
when the browser marks the request cross-site or its `Origin` does not match the requested
host and port. Requests without an `Origin` (curl, scripts using `X-Api-Key`) are unaffected;
if you call the API from a script through a browser-like client, do not send a foreign
`Origin` header.

> **Warning: "Disable login for local addresses" is unsafe behind Docker port mapping or a
> reverse proxy.** In those setups every request reaches the app from a local address (the
> Docker bridge or the proxy), so enabling this option turns authentication off for anyone who
> can reach the port, including the internet. Only enable it when the app is reachable
> exclusively on a trusted LAN.

Overlaparr deliberately ignores `X-Forwarded-For` and similar proxy headers: only the actual
network peer decides whether a request is "local", so a client can't fake a local address
through a header. As a result, client IPs in the access log are your proxy's address.

## Notifications

Add connections under **Notifications**: email (SMTP), Discord, generic webhook, or Apprise
(which covers many other services). For each connection choose which events trigger it (new
findings, automation actions, automation deletes, findings lost, scan problems, app errors).
Each connection can also send a scheduled digest (daily, weekly or cron) that summarises
findings and recent activity, in addition to its event messages. The **Test** button sends
a test message and records the result in the delivery history.

## Logs

Logs are JSON lines written to stdout (`docker logs overlaparr`) and to
`/config/logs/overlaparr.log`. The level (default `INFO`) and rotation (size per file and
number of backups) are set in **Settings**. Secrets Overlaparr knows about are redacted from
log lines: every value stored in `secrets.yaml`, keyed fields (`api_key`, `apikey`, `token`,
`password`, `secret=...`), `Authorization`/`Bearer` values, and Discord webhook tokens. Exact
values shorter than 6 characters are not matched as standalone text, and a secret that reaches
a message in an unrecognised shape can still appear, so treat the log as sensitive. The
**Logs** page shows recent entries and lets you download the log file.

## Development

- Backend (Python 3.12, [uv](https://docs.astral.sh/uv/)):

  ```sh
  cd backend
  uv run pytest
  uv run uvicorn overlaparr.main:app_factory --factory --port 8686
  ```

  Set `OVERLAPARR_CONFIG_DIR` to a scratch directory when running locally.

- Frontend (Node 22+):

  ```sh
  cd frontend
  npm ci
  npm test
  npm run dev     # dev server; proxies /api and /health to localhost:8686
  npm run build   # writes the bundle to backend/overlaparr/static
  ```

- Image: `docker build -t overlaparr:dev .`
