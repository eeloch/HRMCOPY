#!/usr/bin/env bash
# Processes yesterday's attendance events into DailyAttendance records.
# Invoked by rotic-hrm-process-attendance.service (see the matching .timer).
set -euo pipefail

cd /opt/rotic_hrm
WORK_DATE="$(date --date=yesterday +%Y-%m-%d)"
/opt/rotic_hrm/venv/bin/python manage.py process_attendance --date "$WORK_DATE"

# In the first days of a month this proposes last month's automatic "No absence" / "No lateness" rewards for
# approval and notifies the approvers. It does nothing on other days, and a failure here must never make the
# attendance run itself look failed.
/opt/rotic_hrm/venv/bin/python manage.py propose_attendance_rewards || echo "propose_attendance_rewards failed (attendance itself was processed)" >&2
