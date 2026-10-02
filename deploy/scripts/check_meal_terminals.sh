#!/usr/bin/env bash
# Alerts superusers when a meal terminal has been out of contact for a few minutes.
# Invoked every minute by rotic-hrm-check-meal-terminals.service (see the matching .timer).
set -euo pipefail

cd /opt/rotic_hrm
exec /opt/rotic_hrm/venv/bin/python manage.py check_meal_terminals
