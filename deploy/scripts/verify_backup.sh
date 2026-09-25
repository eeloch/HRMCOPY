#!/usr/bin/env bash
# Prove the newest database backup matches the live database: reads every row count of the important tables straight
# out of the dump and compares them with the running database. Needs no superuser and touches nothing.
#
#   sudo -u rotic bash -c 'cd /opt/rotic_hrm && set -a && source .env && set +a && deploy/scripts/verify_backup.sh'
#
# A dump legitimately lags the live data by up to a day, so small differences in busy tables (punches, audit events,
# device commands) are normal; a table that is empty or far off in the dump is not. This is not a full restore drill:
# that needs a database role that may CREATE DATABASE (restore into a scratch database and start the app against it).
set -euo pipefail

BACKUP_DIR="/var/backups/rotic-hrm"
LATEST="$(ls -1t "$BACKUP_DIR"/db_*.dump | head -1)"
echo "Newest backup: $LATEST ($(date -r "$LATEST" '+%Y-%m-%d %H:%M'))"
pg_restore --list "$LATEST" > /dev/null && echo "Catalog readable: yes"

TABLES="employees_employee employees_biometricidentity payroll_payrollperiod payroll_employeepayroll meals_mealcollection attendance_dailyattendance leave_leaverequest audit_auditevent"
printf '%-34s %10s %10s\n' table dump live
for table in $TABLES; do
    in_dump="$(pg_restore --data-only -t "$table" -f - "$LATEST" 2>/dev/null | awk '/^COPY/{f=1;next} f&&/^\\\./{exit} f{n++} END{print n+0}')"
    live="$(PGPASSWORD="${DB_PASSWORD:-}" psql -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" -tAc "select count(*) from $table")"
    printf '%-34s %10s %10s\n' "$table" "$in_dump" "$live"
done
