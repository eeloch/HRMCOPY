#!/usr/bin/env bash
# Processes yesterday's attendance events into DailyAttendance records.
# Invoked by rotic-hrm-process-attendance.service (see the matching .timer).
set -euo pipefail

cd /opt/rotic_hrm
WORK_DATE="$(date --date=yesterday +%Y-%m-%d)"
exec /opt/rotic_hrm/venv/bin/python manage.py process_attendance --date "$WORK_DATE"
