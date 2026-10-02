"""Alert superusers when a meal terminal has been out of contact for a few minutes during the day.

A terminal that loses its connection keeps recognising faces and printing tickets, but nothing reaches us, so
the switch-off after someone's last meal cannot be delivered (2026-10-01: one person collected 5 tickets against an
entitlement of 2 during a 25-minute outage). Run every minute by rotic-hrm-check-meal-terminals.timer. One alert
per outage; a second notification says when it is back."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from attendance.models import BiometricDevice
from notifications.models import Notification, NotificationSeverity
from notifications.services import NotificationService

OFFLINE_AFTER = timedelta(minutes=3)
ALERT_FROM_HOUR, ALERT_UNTIL_HOUR = 5, 22  # local time; a terminal switched off overnight is not an incident
OFFLINE_EVENT = "meals.terminal_offline"
BACK_EVENT = "meals.terminal_back_online"


def check(now=None):
    now = now or timezone.now()
    local_hour = timezone.localtime(now).hour
    recipients = list(get_user_model().objects.filter(is_superuser=True, is_active=True))
    sent = []
    for device in BiometricDevice.objects.filter(purpose="meal_ticket"):
        last_contact = device.last_sync_at
        down = not device.is_online and (last_contact is None or now - last_contact > OFFLINE_AFTER)
        open_alert = Notification.objects.filter(event_type=OFFLINE_EVENT, metadata__serial=device.serial_number, metadata__resolved=False).exists()
        if down and ALERT_FROM_HOUR <= local_hour < ALERT_UNTIL_HOUR and not open_alert:
            since = timezone.localtime(last_contact).strftime("%H:%M on %d %b") if last_contact else "an unknown time"
            for user in recipients:
                NotificationService.create(
                    recipient=user,
                    event_type=OFFLINE_EVENT,
                    title=f"Meal terminal offline: {device.name}",
                    message=f"{device.name} ({device.serial_number}) has not been in contact since {since}. It still prints tickets locally, but scans are not being recorded and people are not switched off after their last meal. Check its power and network.",
                    severity=NotificationSeverity.ERROR,
                    related_url="/devices",
                    metadata={"serial": device.serial_number, "resolved": False},
                )
            sent.append(f"offline: {device.name}")
        elif not down and open_alert:
            Notification.objects.filter(event_type=OFFLINE_EVENT, metadata__serial=device.serial_number, metadata__resolved=False).update(
                metadata={"serial": device.serial_number, "resolved": True}
            )
            for user in recipients:
                NotificationService.create(
                    recipient=user,
                    event_type=BACK_EVENT,
                    title=f"Meal terminal back online: {device.name}",
                    message=f"{device.name} ({device.serial_number}) is connected again. Any scans it stored while offline are imported on reconnect - review the Needs a decision queue.",
                    severity=NotificationSeverity.SUCCESS,
                    related_url="/meals",
                    metadata={"serial": device.serial_number},
                )
            sent.append(f"back online: {device.name}")
    return sent


class Command(BaseCommand):
    help = "Notify superusers when a meal terminal has been out of contact for more than a few minutes."

    def handle(self, *args, **options):
        for line in check():
            self.stdout.write(line)
