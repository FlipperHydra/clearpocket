#!/usr/bin/env bash
# DESTRUCTIVE: replaces the current database contents with a backup made by scripts/backup.sh.
# Usage: scripts/restore.sh backups/clearpocket-YYYYMMDD-HHMMSS.dump
set -euo pipefail
cd "$(dirname "$0")/.."
file="${1:-}"
[ -n "$file" ] && [ -s "$file" ] || { echo "Usage: $0 <backup.dump>" >&2; exit 2; }
echo "This will OVERWRITE all current ClearPocket data with: $file"
read -r -p "Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "Aborted."; exit 1; }
docker compose stop app
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error' < "$file"
docker compose up -d
echo "Restore complete."
