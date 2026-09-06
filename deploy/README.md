# Deploying Rotic HRM to a VPS

Follow this in order. Everywhere you see `<...>`, replace it - and everywhere
you see `rotic` as a username or `/opt/rotic_hrm` as a path, swap in your own
if you'd rather use different ones (just keep it consistent across every
file in this folder).

## 1. Provision the box

Ubuntu 24.04 LTS, a non-root sudo user, and a firewall that only allows what
you actually need:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
```

## 2. Install the runtime

```bash
sudo apt update
sudo apt install python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx nodejs npm
```

## 3. Create the app user, database, and clone the repo

```bash
sudo useradd --system --create-home --shell /bin/bash rotic
sudo -u postgres createuser rotic_hrm_app --pwprompt
sudo -u postgres createdb rotic_hrm --owner=rotic_hrm_app

sudo -u rotic git clone <your-repo-url> /opt/rotic_hrm
cd /opt/rotic_hrm
```

## 4. Set up the environment file

```bash
sudo -u rotic cp .env.example .env
sudo -u rotic nano .env
```

Fill in, at minimum: `DJANGO_DEBUG=False`, a fresh `DJANGO_SECRET_KEY`
(generate one, don't reuse the one in git history), `DJANGO_ALLOWED_HOSTS`
(your domain), `DJANGO_CORS_ALLOWED_ORIGINS` (your domain, `https://`),
`DB_NAME`/`DB_USER`/`DB_PASSWORD` (the Postgres role from step 3),
`BIOMETRIC_BRIDGE_SECRET` (a long random value - the vendor gateway needs
the same one), and `DJANGO_SECURE_SSL_REDIRECT=True` once HTTPS is live in
step 8.

`chmod 600 .env` - it holds every secret this app has.

## 5. Backend: install, migrate, seed roles

```bash
sudo -u rotic python3 -m venv /opt/rotic_hrm/venv
sudo -u rotic /opt/rotic_hrm/venv/bin/pip install -r requirements.txt gunicorn
sudo -u rotic /opt/rotic_hrm/venv/bin/python manage.py migrate
sudo -u rotic /opt/rotic_hrm/venv/bin/python manage.py collectstatic --noinput
sudo -u rotic /opt/rotic_hrm/venv/bin/python manage.py createsuperuser
sudo -u rotic /opt/rotic_hrm/venv/bin/python manage.py seed_roles
```

`seed_roles` creates the standard Groups (Timekeeper, Supervisor, HR
Manager, Payroll Officer, PPE/Store Officer, Meals Coordinator) with the
right permissions already attached - assign real staff accounts to them
from `/admin/` (Users → pick a user → Groups) rather than granting
permissions one at a time.

## 6. Frontend: build

```bash
cd /opt/rotic_hrm/frontend
sudo -u rotic bash -c 'echo "NEXT_PUBLIC_API_URL=https://<your-domain>/api" > .env.production.local'
sudo -u rotic npm install
sudo -u rotic npm run build
cd /opt/rotic_hrm
```

## 7. systemd services

```bash
sudo chmod +x deploy/scripts/*.sh
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rotic-hrm-backend.service
sudo systemctl enable --now rotic-hrm-frontend.service
sudo systemctl enable --now rotic-hrm-process-attendance.timer
sudo systemctl enable --now rotic-hrm-pg-backup.timer
```

Check they're actually up: `systemctl status rotic-hrm-backend.service` and
`journalctl -u rotic-hrm-backend.service -f`.

## 8. nginx + HTTPS

```bash
sudo cp deploy/nginx/rotic-hrm.conf /etc/nginx/sites-available/rotic-hrm.conf
# edit the <your-domain> and /opt/rotic_hrm placeholders in that file first
sudo ln -s /etc/nginx/sites-available/rotic-hrm.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d <your-domain>
```

After certbot succeeds, set `DJANGO_SECURE_SSL_REDIRECT=True` in `.env` (if
you hadn't already) and `sudo systemctl restart rotic-hrm-backend.service`.

Read the comments in `deploy/nginx/rotic-hrm.conf` before touching it - it
deliberately does **not** serve `/media/` directly. Employee documents only
go through the authenticated Django download view; a plain `/media/` alias
would serve national ID scans and medical certificates to anyone with the
URL, no login required.

## 9. Verify

- `https://<your-domain>/admin/` - log in with the superuser from step 5.
- `https://<your-domain>/` - the frontend, redirects to `/login`.
- `curl -I https://<your-domain>/api/auth/me/` - should be `401` when
  logged out (proves the API isn't wide open).
- `sudo -u rotic /opt/rotic_hrm/venv/bin/python manage.py check --deploy` -
  should report no warnings once `.env` is filled in correctly.

## What's in this folder

| File | Purpose |
|---|---|
| `nginx/rotic-hrm.conf` | Reverse proxy: `/api/`+`/admin/` → gunicorn, `/static/` → collected static files, everything else → the Next.js frontend. No direct `/media/` access. |
| `systemd/rotic-hrm-backend.service` | Runs gunicorn (Django). |
| `systemd/rotic-hrm-frontend.service` | Runs `next start`. |
| `systemd/rotic-hrm-process-attendance.{service,timer}` | Runs `manage.py process_attendance` for yesterday, daily at 01:00. There's no Celery in this project - this timer is the only thing that ever calls it. |
| `systemd/rotic-hrm-pg-backup.{service,timer}` | Dumps Postgres + tars `media/`, daily at 02:30, keeps 14 days locally. |
| `scripts/process_attendance.sh`, `scripts/pg_backup.sh` | The actual commands the two timers above run. |

## Things this folder deliberately doesn't cover

- **Off-box backup storage.** `pg_backup.sh` only writes to local disk. Sync
  `/var/backups/rotic-hrm` somewhere else (another server, S3, Backblaze -
  whatever you already use) on the same schedule, or a disk failure takes
  the backups down with the database.
- **Outbound email.** `MAILERS` is still the console backend. Add real SMTP
  settings before relying on any email notification.
- **Error tracking.** Consider Sentry (or similar) so exceptions surface
  somewhere once `DEBUG=False` stops showing them in the browser.
