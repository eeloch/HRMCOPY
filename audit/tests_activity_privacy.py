from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.test import APITestCase

from audit.models import AuditEvent


class ActivityFeedFinancialPrivacyTests(APITestCase):
    """2026-09-25 review: any login could read bonus/advance/payroll amounts in the global activity feed."""

    def setUp(self):
        User = get_user_model()
        AuditEvent.objects.create(event_type="payroll.adjusted", module="payroll", title="Adjustment", description="Deduction of N 40,000")
        AuditEvent.objects.create(event_type="bonus.approved", module="bonuses", title="Bonus", description="N 100,000 approved")
        AuditEvent.objects.create(event_type="attendance.exception_waived", module="attendance", title="Waived", description="late waived")
        self.reader = User.objects.create_user(username="reader", password="x")
        self.payroll = User.objects.create_user(username="payroller", password="x")
        self.payroll.user_permissions.add(Permission.objects.get(codename="view_payroll", content_type__app_label="payroll"))
        self.boss = User.objects.create_superuser(username="boss", password="x", email="b@x.co")

    def modules_seen(self, user):
        self.client.force_authenticate(user)
        response = self.client.get("/api/audit/activity/")
        self.assertEqual(response.status_code, 200)
        return {item["module"] for item in response.data["results"]}

    def test_an_ordinary_login_sees_no_financial_events(self):
        self.assertEqual(self.modules_seen(self.reader), {"attendance"})

    def test_search_and_module_filters_cannot_reach_hidden_events(self):
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.get("/api/audit/activity/?module=payroll").data["count"], 0)
        self.assertEqual(self.client.get("/api/audit/activity/?search=100,000").data["count"], 0)

    def test_a_payroll_user_sees_payroll_but_not_other_modules_they_lack(self):
        self.assertEqual(self.modules_seen(self.payroll), {"attendance", "payroll"})

    def test_a_superuser_sees_everything(self):
        self.assertEqual(self.modules_seen(self.boss), {"attendance", "payroll", "bonuses"})
