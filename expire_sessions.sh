#!/usr/bin/env bash
# Test helper: expire every session in the database (simulates idle/absolute timeout).
# Set COMPOSE_ENV_FILE / COMPOSE_PROJECT_NAME to target a non-default stack.
set -euo pipefail
cd "$(dirname "$0")/.."
args=()
[ -n "${COMPOSE_ENV_FILE:-}" ] && args=(--env-file "$COMPOSE_ENV_FILE")
docker compose ${args[@]+"${args[@]}"} exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' \
  <<< "update sessions set expires_at = now() - interval '1 second';"
