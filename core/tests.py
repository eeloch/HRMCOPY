from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient


class CurrentUserAPIViewTests(TestCase):
    endpoint = "/api/auth/me/"
    permission_codes = (
        "record_meal_operations",
        "review_meal_excess",
        "manage_meal_configuration",
    )

    def setUp(self):
        self.client = APIClient()

    def create_user(self, username="current-user"):
        return get_user_model().objects.create_user(
            username=username,
            password="password",
        )

    def permission(self, codename):
        return Permission.objects.get(
            content_type__app_label="meals",
            codename=codename,
        )

    def capabilities(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 401)

    def test_authenticated_user_receives_identity(self):
        user = self.create_user()

        response = self.capabilities(user)

        self.assertEqual(response["id"], user.pk)
        self.assertEqual(response["username"], user.username)
        self.assertFalse(response["is_superuser"])

    def test_user_without_meal_permissions_receives_false_capabilities(self):
        response = self.capabilities(self.create_user("no-meal-permissions"))

        self.assertEqual(
            response["permissions"],
            {code: False for code in self.permission_codes},
        )

    def test_record_meal_operations_capability_uses_django_permission(self):
        user = self.create_user("meal-recorder")
        user.user_permissions.add(
            self.permission("record_meal_operations")
        )

        response = self.capabilities(user)

        self.assertTrue(response["permissions"]["record_meal_operations"])
        self.assertFalse(response["permissions"]["review_meal_excess"])
        self.assertFalse(response["permissions"]["manage_meal_configuration"])

    def test_review_meal_excess_capability_uses_django_permission(self):
        user = self.create_user("meal-reviewer")
        user.user_permissions.add(self.permission("review_meal_excess"))

        response = self.capabilities(user)

        self.assertFalse(response["permissions"]["record_meal_operations"])
        self.assertTrue(response["permissions"]["review_meal_excess"])
        self.assertFalse(response["permissions"]["manage_meal_configuration"])

    def test_manage_meal_configuration_capability_uses_django_permission(self):
        user = self.create_user("meal-configurator")
        user.user_permissions.add(
            self.permission("manage_meal_configuration")
        )

        response = self.capabilities(user)

        self.assertFalse(response["permissions"]["record_meal_operations"])
        self.assertFalse(response["permissions"]["review_meal_excess"])
        self.assertTrue(response["permissions"]["manage_meal_configuration"])

    def test_superuser_reports_superuser_and_django_capabilities(self):
        user = get_user_model().objects.create_superuser(
            username="meal-superuser",
            email="meal-superuser@example.com",
            password="password",
        )

        response = self.capabilities(user)

        self.assertTrue(response["is_superuser"])
        self.assertEqual(
            response["permissions"],
            {code: True for code in self.permission_codes},
        )

    def test_response_excludes_sensitive_user_fields(self):
        response = self.capabilities(self.create_user("private-user"))

        self.assertEqual(
            set(response),
            {"id", "username", "is_superuser", "permissions"},
        )
        self.assertNotIn("password", response)
        self.assertNotIn("email", response)
        self.assertNotIn("groups", response)
