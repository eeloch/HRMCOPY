# Operations runbook

Server: DigitalOcean droplet, Ubuntu 24.04, app in `/opt/rotic_hrm` (Python 3.12 venv, Node 20). Public URL
https://hrm.roticaluminium.com (nginx + certbot in front of gunicorn on 127.0.0.1:8000 and Next.js on :3000).

## Services (systemd)
| Unit | What |
|---|---|
| `rotic-hrm-backend` | gunicorn, Django API (2 workers) |
| `rotic-hrm-frontend` | Next.js server |
| `rotic-hrm-aiface-gateway` | websocket server for the biometric terminals on port 7788 (`manage.py run_aiface_gateway`) |
| `rotic-hrm-process-attendance.timer` | processes attendance on a schedule |
| `rotic-hrm-pg-backup.timer` | nightly database dump + media copy, 02:30 UTC, kept 14 days in `/var/backups/rotic-hrm` |

## Deploying a change
1. Make sure CI is green on `main`.
2. On the server: `cd /opt/rotic_hrm && git pull`.
3. `source venv/bin/activate && set -a && source .env && set +a`, then `python manage.py migrate` (see the plan first with `migrate --plan`).
4. If the frontend changed: `cd frontend && npm run build`.
5. Restart what changed: `systemctl restart rotic-hrm-backend rotic-hrm-frontend` and, for terminal/gateway code, `rotic-hrm-aiface-gateway`.
   Restarting the gateway drops every terminal for a few seconds; they reconnect by themselves.
6. Check: `systemctl show <unit> -p ActiveEnterTimestamp` is after the deploy, and `python manage.py check --deploy` is clean.

Rollback: `git checkout <previous commit>`, rebuild the frontend if needed, restart. A migration that has been applied is
not rolled back automatically; check `migrate --plan` before deploying anything that adds one.

## Settings worth knowing (environment, `.env`)
`DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_NUM_PROXIES` (default 1: one proxy), `DJANGO_LOGIN_THROTTLE_RATE`,
`DJANGO_LOGIN_USER_THROTTLE_RATE`, `DJANGO_CACHE_DIR` (shared throttle cache, default `/var/tmp/rotic_hrm_cache`),
`AIFACE_ALLOWED_NETWORKS` (optional CIDR allow-list for the terminal gateway), `MEAL_GATING_EMPLOYEE_IDS` (`*` = everyone).

## Backups
- Nightly dump: `deploy/scripts/pg_backup.sh` (fails the unit if the dump cannot be read back).
- Prove the newest dump matches the live data: `deploy/scripts/verify_backup.sh`.
- Restore into a scratch database needs a role that may create databases; the app's database user cannot, so use the
  `postgres` superuser on the server. Never restore over the live database without a fresh dump first.
- TODO: copy the backups off the server (encrypted) and record a restore drill.

## Biometric terminals
- Rule: the staff number (without leading zeros) is the user id on every terminal.
- `manage.py device_coverage queue|report` asks every terminal what it holds and shows the gaps.
- `manage.py mirror_plan --probe`, then `mirror_plan` (dry run) and `--apply`: removes leavers' entries and moves leftover
  copies onto the owner's staff number. Names are read from the terminals; nothing biometric is stored.
- `manage.py renumber_ids`, `repair_duplicate_ids`: same idea for identities that are not on the staff number.
- `manage.py resend_switches --device NAME --apply`: re-send the meal switch-off to everyone recorded as off.
- `manage.py remove_duplicate_employee DUP --keep KEEP [--confirm]`: delete a duplicate employee after its history was moved.
- The gateway logs `command poller stalled` when a terminal's command sender stops and closes the connection so the
  terminal reconnects; `refused reg` when it turns a connection away.

## When something is wrong
- A terminal shows offline: check power and network at the terminal first; the log shows close code 1006 when the link
  drops without a goodbye.
- Extra meal tickets: find the person's scan times and the `set_user_enabled` commands for their terminal id; compare
  the scan time with the switch-off's acknowledgement time.
- Nothing saves in the admin: the admin actions ask for confirmation first; look at the audit trail for the result.
