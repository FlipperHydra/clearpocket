#!/usr/bin/env bash
# Logical backup of the ClearPocket database (custom pg_dump format, restorable with scripts/restore.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
umask 077
out="${1:-backups/clearpocket-$(date +%Y%m%d-%H%M%S).dump}"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' > "$out"
if [ ! -s "$out" ]; then rm -f "$out"; echo "Backup failed: empty output" >&2; exit 1; fi
docker compose exec -T db pg_restore --list < "$out" > /dev/null
echo "Backup written to $out ($(wc -c < "$out") bytes). Copy it off this machine; it contains all user data."
