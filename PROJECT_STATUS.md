# Project Status

# ROTIC HRM

Status: pilot in production at hrm.roticaluminium.com (single DigitalOcean droplet). Last updated: 25 Sep 2026.
Readiness: see the 25 Sep 2026 company-readiness review; the security findings from it are fixed in the code (see
"Security" below) and the remaining infrastructure items are listed under "Open".

## What exists
Django (DRF, JWT) backend and a Next.js 16 frontend, PostgreSQL in production.

| Area | State |
|---|---|
| Employees | Directory, bulk import (partial updates; never touches salary), salary import, departments/positions, personal details behind `employees.view_employee`, salary and bank details behind their own permissions |
| Attendance | Rosters and rotation plans, daily attendance processing (night shifts), exceptions with approval, overtime, biometric devices |
| Biometric terminals | Three attendance terminals plus two meal terminals (AiFace, websocket gateway on port 7788). Staff number = terminal user id on every terminal. Credentials are synced between terminals, leftovers and duplicates can be cleaned with `mirror_plan`, `renumber_ids`, `repair_duplicate_ids` |
| Meals | Ticket entitlements per person, tickets collected at the meal terminals, excess review and deductions, terminal switch-off once a person's tickets are used (both the profile and the legacy command are sent) |
| Payroll | Periods, generation by employment dates, attendance deductions, approval (blocked until rosters are complete and deductions synced; bank details are snapshotted at approval), bank-upload export |
| Money workflows | Salary advances, deferred funds, bonuses and Employee of the Month, PPE deductions, offences |
| Leave, documents, accommodation | Leave policies and approvals, protected document storage, hostel rooms and assignments |
| Audit and notifications | Audit trail for sensitive actions (financial modules visible only to users with those permissions), in-app notifications |
| Reports | Weekly HR report deck |
| Django admin | Every model has readable rows (staff number + name), search, filters and safe corrective actions that go through the same services as the API, each writing an audit event |

## Quality gates
`.github/workflows/ci.yml` runs on every push and pull request: Django check, migration drift, `pip check`, the full test
suite (900+ tests), TypeScript, ESLint with zero warnings allowed, and the production build. Nothing should be deployed
from a red main.

## Security (from the 25 Sep 2026 review)
- Login throttling trusts only the address nginx appends (`DJANGO_NUM_PROXIES=1`), is counted in a cache shared by all
  gunicorn workers, and also limits attempts per account.
- Activity feed hides financial modules from users without the matching permissions.
- Personal details, room placement by import, and creating departments/positions each need their own permission.
- XLSX exports keep untrusted text literal; staff numbers that start with `=`, `+`, `-` or `@` are rejected.
- Terminal gateway: a connected terminal cannot be taken over from another address, a connection cannot switch serial
  numbers, and punches must come from a registered connection. `AIFACE_ALLOWED_NETWORKS` (comma-separated CIDRs) is an
  optional allow-list; empty means anyone can connect, because the factory's public address can change.

## Open
- Port 7788 is reachable from the whole internet. Restrict it at the firewall to the factory's address range once that
  range is confirmed (and/or set `AIFACE_ALLOWED_NETWORKS`).
- Backups are local only (14 days) and unencrypted; an off-server encrypted copy and a full restore drill are still to do.
- The GitHub repository is public.
- Payroll roles are not separated (the same user can create, approve and export a payroll run).
- Browser tokens are kept in localStorage.
- No frontend tests.
