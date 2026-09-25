#!/usr/bin/env bash
# Nightly Postgres dump + a copy of MEDIA_ROOT (uploaded employee
# documents), kept for 14 days locally. Invoked by
# rotic-hrm-pg-backup.service (see the matching .timer).
#
# This only writes backups to local disk under BACKUP_DIR - copy that
# directory to off-box storage (another server, S3, etc.) on the same
# schedule, or a disk failure takes the backups down with the database.
set -euo pipefail

BACKUP_DIR="/var/backups/rotic-hrm"
MEDIA_ROOT="/opt/rotic_hrm/media"
KEEP_DAYS=14
STAMP="$(date +%Y-%m-%d_%H%M)"

mkdir -p "$BACKUP_DIR"

# DB_NAME/DB_USER/DB_HOST/DB_PORT come from the same .env the app uses -
# see rotic-hrm-pg-backup.service's EnvironmentFile.
PGPASSWORD="${DB_PASSWORD:-}" pg_dump \
    --host="${DB_HOST:-localhost}" \
    --port="${DB_PORT:-5432}" \
    --username="${DB_USER}" \
    --format=custom \
    --file="${BACKUP_DIR}/db_${STAMP}.dump" \
    "${DB_NAME}"

# A dump that cannot be read back is not a backup: fail loudly (the systemd unit then shows as failed) instead of
# leaving a corrupt file to be discovered on the day it is needed.
pg_restore --list "${BACKUP_DIR}/db_${STAMP}.dump" > /dev/null
if [ "$(stat -c %s "${BACKUP_DIR}/db_${STAMP}.dump")" -lt 10000 ]; then
    echo "Backup ${BACKUP_DIR}/db_${STAMP}.dump is suspiciously small" >&2
    exit 1
fi

tar -czf "${BACKUP_DIR}/media_${STAMP}.tar.gz" -C "$(dirname "$MEDIA_ROOT")" "$(basename "$MEDIA_ROOT")"

find "$BACKUP_DIR" -type f -mtime "+${KEEP_DAYS}" -delete

echo "Backed up to ${BACKUP_DIR}/{db,media}_${STAMP}.*"
