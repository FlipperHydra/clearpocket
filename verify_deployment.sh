#!/usr/bin/env bash
# Full deployment verification in an ISOLATED throw-away project (own volume, port 18000, random secrets).
# Never touches your real stack. Requires: docker compose v2, curl, python3.
#   scripts/verify_deployment.sh            # leaves the verify stack running
#   scripts/verify_deployment.sh --cleanup  # afterwards removes ONLY the verify project incl. its volume
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=clearpocket-verify
ENV_FILE=".env.verify"
PORT=18000
BASE="http://127.0.0.1:${PORT}"
STATE=".smoke-state.json"

umask 077
cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
APP_PORT=${PORT}
BIND_ADDRESS=127.0.0.1
PUBLIC_ORIGIN=http://127.0.0.1:${PORT}
ENVIRONMENT=local
COOKIE_SECURE=false
EOF
dc() { docker compose --env-file "$ENV_FILE" "$@"; }
step() { printf '\n=== %s ===\n' "$*"; }
wait_healthy() {
  for _ in $(seq 1 60); do curl -fsS "$BASE/api/health" > /dev/null 2>&1 && return 0; sleep 2; done
  dc logs --tail=80; echo "App did not become healthy" >&2; return 1
}

step "1. compose config validates"
dc config -q
step "2. clean build + start: docker compose up --build -d"
dc down --remove-orphans >/dev/null 2>&1 || true
dc up --build -d
wait_healthy
step "3. migrations ran against a fresh database"
dc logs migrate | tail -5
dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"'
step "4. database port is NOT published"
if dc port db 5432 2>/dev/null | grep -q .; then echo "FAIL: db port published" >&2; exit 1; else echo "ok: no published db port"; fi
step "5. app runs as non-root"
dc exec -T app id -u | grep -vx 0 > /dev/null && echo "ok: uid $(dc exec -T app id -u)"
step "6. API smoke test (auth, CSRF, isolation, totals, months, rate limit, expiry)"
export COMPOSE_ENV_FILE="$ENV_FILE"
python3 scripts/smoke_test.py run --base "$BASE" --state "$STATE" --expire-cmd "scripts/expire_sessions.sh"
step "7. data survives a restart"
dc restart app db
wait_healthy
python3 scripts/smoke_test.py verify --base "$BASE" --state "$STATE"
step "8. data survives container recreation (down WITHOUT -v, then up)"
dc down
dc up -d
wait_healthy
python3 scripts/smoke_test.py verify --base "$BASE" --state "$STATE"
step "9. rebuilt image + restart does not re-seed or reset data"
dc up --build -d
wait_healthy
python3 scripts/smoke_test.py verify --base "$BASE" --state "$STATE"

echo; echo "ALL DEPLOYMENT CHECKS PASSED"
if [ "${1:-}" = "--cleanup" ]; then
  echo "Cleaning up the verify project only (clearpocket-verify, volume included)..."
  dc down -v --remove-orphans
  rm -f "$ENV_FILE" "$STATE"
fi
