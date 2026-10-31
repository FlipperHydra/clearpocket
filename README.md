# ClearPocket

A self-hosted, multi-user, zero-based budgeting app. One container serves the web UI and the JSON API from the same origin; PostgreSQL stores everything.

```bash
cp .env.example .env          # then set POSTGRES_PASSWORD and SECRET_KEY (see "First run")
docker compose up --build -d
# open http://localhost:8000
```

> **Status:** this project was assembled in an environment without Docker or PostgreSQL. The pure-Python tests (config, money, security, calculations: 41 tests) and a Node check of the frontend helper functions passed. Nothing database-backed, no container build, and no browser test of this exact tree has been run. Run `scripts/verify_deployment.sh --cleanup` and the backend test suite on your machine before relying on it. See `docs/VERIFICATION_REPORT.md`.

## Architecture and layout

```
browser ──HTTP──▶ app container (uvicorn + FastAPI, non-root, port 8000)
                    ├─ serves /, /styles.css, /app.js   (explicit allow-list)
                    └─ /api/*  ──▶ db container (PostgreSQL 16, named volume, NOT published)
migrate container (one-shot: alembic upgrade head) runs after db is healthy and before app starts
```

```
compose.yaml  Dockerfile  .dockerignore  .env.example
clearpocket-budget.html  styles.css  app.js     frontend (plain HTML/CSS/JS, no build step)
backend/      app/ (routers, services, calculations)  migrations/  tests/  requirements*.txt  docs/API.md
scripts/      backup.sh  restore.sh  smoke_test.py  verify_deployment.sh  expire_sessions.sh
docs/         MANUAL_TEST_CHECKLIST.md  VERIFICATION_REPORT.md
```

Money is stored as integer cents. Category activity is derived from transactions. Unspent money carries into the next month; overspending reduces the next month's "Ready to assign". API details: `backend/docs/API.md`.

## Prerequisites

Docker Engine with Compose v2 (`docker compose version`). For tests outside Docker: Python 3.11+ and a throw-away PostgreSQL.

## First run

1. `cp .env.example .env`
2. Put real secrets in `.env` (never commit it):
   - `POSTGRES_PASSWORD`: `openssl rand -hex 24` (hex is URL-safe; it is embedded in `DATABASE_URL`)
   - `SECRET_KEY`: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
3. `docker compose up --build -d`
4. Open `http://localhost:8000` and create an account. New accounts start empty; "Load sample data" is an explicit button shown only for empty budgets.

`up` fails with a clear message if either secret is missing, and the app refuses to start if a secret still contains `CHANGE_ME`.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD` | required | Database password (URL-safe characters) |
| `POSTGRES_USER` / `POSTGRES_DB` | `clearpocket` | Database role / name |
| `SECRET_KEY` | required, 32+ chars | Signs CSRF tokens, keys rate-limit buckets |
| `APP_PORT` | `8000` | Host port for UI + API |
| `BIND_ADDRESS` | `127.0.0.1` | `0.0.0.0` exposes the app to your LAN |
| `PUBLIC_ORIGIN` | `http://localhost:${APP_PORT}` | URL users type; drives the CSRF Origin check |
| `ALLOWED_ORIGINS` | empty | Extra comma-separated origins (default: `PUBLIC_ORIGIN` plus its localhost/127.0.0.1 twin) |
| `ENVIRONMENT` | `local` | `production` enforces HTTPS-only cookies and an https `PUBLIC_ORIGIN` |
| `COOKIE_SECURE` | `false` | `true` behind HTTPS (cookie gets `Secure` and the `__Host-` prefix) |
| `COOKIE_SAMESITE` | `lax` | `lax` or `strict` |
| `SESSION_IDLE_HOURS` / `SESSION_ABSOLUTE_DAYS` | `168` / `30` | Sliding idle timeout / hard lifetime |
| `TRUSTED_PROXY_IPS` | `127.0.0.1` | Who may set `X-Forwarded-*` (see HTTPS section) |
| `WEB_CONCURRENCY` | `2` | uvicorn workers |
| `API_DOCS` | `false` | Serve `/api/docs` |
| `APP_VERSION` | `latest` | Image tag |

**Local HTTP vs HTTPS.** Defaults leave cookies non-`Secure`, which is what lets login work over `http://localhost`. The app validates the combination at startup and refuses `COOKIE_SECURE=true` + a plain-HTTP LAN address (browsers silently drop the cookie) and an `https://` origin with `COOKIE_SECURE=false`. Put the exact host you browse to in `PUBLIC_ORIGIN`; a different hostname gives `403 bad_origin` on login.

## Day-to-day commands

```bash
docker compose up --build -d     # build + start / apply updates
docker compose ps                # status and health
docker compose logs -f app       # follow logs (also: db, migrate)
docker compose restart app       # restart one service, data untouched
docker compose stop              # normal shutdown: containers stopped, data kept
docker compose start             # start again
docker compose down              # remove containers/network, DATA KEPT (named volume)
```

> **Destructive commands: these delete all users and budgets.**
> `docker compose down -v` · `docker compose down --volumes` · `docker volume rm clearpocket_db-data` · `docker volume prune` · `docker system prune --volumes`
> Take a backup first. `stop`, `restart`, `down` (without `-v`), rebuilds and image updates never delete the volume, and sample data is never re-created automatically.

## Migrations and updates

Migrations are Alembic scripts in `backend/migrations/versions`. The one-shot `migrate` service runs `alembic upgrade head` on every `docker compose up`, after PostgreSQL is healthy and before the app starts; if it fails the app does not start (`docker compose logs migrate`). Re-running is a no-op when the schema is current.

```bash
scripts/backup.sh                 # always first
git pull                          # or copy new files in
docker compose up --build -d      # rebuild, migrate, restart app
docker compose ps && docker compose logs --tail=50 app
```
Inspect: `docker compose run --rm migrate alembic current`. Avoid `alembic downgrade` on real data; restore a backup instead. PostgreSQL major-version upgrades (image pinned to `postgres:16-alpine`) need a dump and restore into a new volume.

**Dependency lockfile.** `backend/requirements.txt` uses compatible ranges; no lockfile is committed because it must be generated with network access. Generate one (with hashes) and the Dockerfile will use it:
```bash
docker run --rm -v "$PWD/backend:/w" -w /w python:3.12-slim sh -c \
  "pip install pip-tools && pip-compile --generate-hashes -o requirements.lock requirements.txt"
```

## Tests

```bash
# Backend: unit + API/database tests (needs a throw-away PostgreSQL; its schema is DROPPED each run)
docker run --rm -d --name cp-test-db -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=clearpocket_test -p 127.0.0.1:55432:5432 postgres:16-alpine
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql://postgres:testpw@127.0.0.1:55432/clearpocket_test pytest -q    # expect 0 skipped
docker stop cp-test-db

# Whole deployment in an isolated throw-away project (port 18000, own volume and secrets):
scripts/verify_deployment.sh --cleanup

# API smoke test against any running stack (creates two random users):
python3 scripts/smoke_test.py run --base http://localhost:8000
```
`verify_deployment.sh` runs: `compose config`, clean build + start, migration check, unpublished-DB-port check, non-root check, the smoke test (auth, CSRF, validation, two-user isolation, totals, carry-forward, overspending, rate limit, session expiry), then persistence across `restart`, `down`/`up` without `-v`, and a rebuild. Rate limits live in the database, so run the smoke test at most about twice per 15 minutes per stack. Browser checks: `docs/MANUAL_TEST_CHECKLIST.md`.

## Backup and restore

```bash
scripts/backup.sh                              # -> backups/clearpocket-YYYYMMDD-HHMMSS.dump (mode 600)
scripts/restore.sh backups/<file>.dump         # DESTRUCTIVE: asks you to type RESTORE
```
Raw equivalents:
```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backup.dump
docker compose stop app
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < backup.dump
docker compose up -d
```
Backups contain all user data and password hashes: store them encrypted and off the host, and test a restore in a scratch project.

## HTTPS behind a reverse proxy

Terminate TLS in a proxy (Caddy, nginx, Traefik). In `.env`:
```
ENVIRONMENT=production
COOKIE_SECURE=true
PUBLIC_ORIGIN=https://budget.example.com
BIND_ADDRESS=127.0.0.1
TRUSTED_PROXY_IPS=<address the app sees the proxy as>
```
Caddy: `budget.example.com { reverse_proxy 127.0.0.1:8000 }`. nginx: `proxy_pass http://127.0.0.1:8000;` with `Host`, `X-Forwarded-For $remote_addr` and `X-Forwarded-Proto $scheme` headers; add HSTS there if wanted.

**Trusted-proxy gotcha.** A proxy on the Docker host reaches the app from the Docker bridge gateway, not 127.0.0.1. Find it: `docker network inspect clearpocket_default --format '{{(index .IPAM.Config 0).Gateway}}'` and set `TRUSTED_PROXY_IPS` to it. Don't use `*` unless only your proxy can reach the app port. If the proxy isn't trusted, all users appear to share one IP and the per-IP login limit (30 / 15 min) hits everyone; if trusted too broadly, clients can spoof IPs. Apply `.env` changes with `docker compose up -d`. Check with `python3 scripts/smoke_test.py run --base https://budget.example.com --https`.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `up` stops with "Set POSTGRES_PASSWORD / SECRET_KEY in .env" | `.env` missing or empty values |
| App exits at start with `ConfigError` | `docker compose logs app`; usually a placeholder secret or cookie/origin mismatch |
| Login seems to succeed but you are signed out | `COOKIE_SECURE=true` on plain HTTP; set `false` locally or use HTTPS |
| `403 bad_origin` on login | `PUBLIC_ORIGIN`/`ALLOWED_ORIGINS` don't match the address bar URL |
| `429 rate_limited` | 5 failed logins per account or 30 attempts per IP in 15 min; wait, or fix `TRUSTED_PROXY_IPS` |
| `migrate` fails | `docker compose logs migrate`; the app won't start until it succeeds |
| Port in use | Change `APP_PORT` |
| Changed `POSTGRES_PASSWORD`, app can't connect | The password is fixed when the volume is first created; revert it or `ALTER ROLE` in PostgreSQL |

## Known limitations

- One budget per user, USD only; no password reset or e-mail verification; no transaction editing (create/delete only); no account deletion.
- Registration reveals whether an e-mail exists (rate limited).
- No Content-Security-Policy (the page loads a font from Fontshare), no request-size limit, no CAPTCHA.
- No built-in TLS, scheduled backups or monitoring.
- No automated browser (end-to-end) tests are included; use the manual checklist.
