from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from core.permissions_registry import MANAGED_PERMISSION_CODENAMES


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
            {code: False for code in MANAGED_PERMISSION_CODENAMES},
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
            {code: True for code in MANAGED_PERMISSION_CODENAMES},
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


class UserAccountManagementAPITests(TestCase):
    list_endpoint = "/api/auth/users/"

    def setUp(self):
        self.client = APIClient()
        self.superuser = get_user_model().objects.create_superuser(
            username="root-admin", email="root@example.com", password="password",
        )
        self.regular_user = get_user_model().objects.create_user(
            username="regular-user", password="password",
        )

    def detail_endpoint(self, user_id):
        return f"/api/auth/users/{user_id}/"

    def test_unauthenticated_request_is_rejected(self):
        self.assertEqual(self.client.get(self.list_endpoint).status_code, 401)

    def test_non_superuser_cannot_list_accounts(self):
        self.client.force_authenticate(self.regular_user)
        self.assertEqual(self.client.get(self.list_endpoint).status_code, 403)

    def test_non_superuser_cannot_create_an_account(self):
        self.client.force_authenticate(self.regular_user)
        response = self.client.post(self.list_endpoint, {"username": "new-hire"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_list_accounts_with_permission_map(self):
        self.regular_user.user_permissions.add(Permission.objects.get(codename="view_salary", content_type__app_label="employees"))
        self.client.force_authenticate(self.superuser)

        response = self.client.get(self.list_endpoint)

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.data["results"] if item["username"] == "regular-user")
        self.assertTrue(row["permissions"]["view_salary"])
        self.assertFalse(row["permissions"]["manage_devices"])
        self.assertIn("permission_registry", response.data)

    def test_superuser_can_create_an_account_with_specific_permissions(self):
        self.client.force_authenticate(self.superuser)

        response = self.client.post(self.list_endpoint, {
            "username": "new-hire",
            "permissions": ["view_salary", "manage_devices"],
        }, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertIn("temporary_password", response.data)
        new_user = get_user_model().objects.get(username="new-hire")
        self.assertFalse(new_user.is_staff)
        self.assertFalse(new_user.is_superuser)
        self.assertTrue(new_user.has_perm("employees.view_salary"))
        self.assertTrue(new_user.has_perm("attendance.manage_devices"))
        self.assertFalse(new_user.has_perm("payroll.view_payroll"))
        self.assertTrue(new_user.check_password(response.data["temporary_password"]))

    def test_cannot_create_a_duplicate_username(self):
        self.client.force_authenticate(self.superuser)
        response = self.client.post(self.list_endpoint, {"username": "regular-user"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_cannot_grant_an_unknown_permission(self):
        self.client.force_authenticate(self.superuser)
        response = self.client.post(self.list_endpoint, {
            "username": "new-hire-2", "permissions": ["delete_everything"],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username="new-hire-2").exists())

    def test_superuser_can_update_an_accounts_permissions(self):
        self.regular_user.user_permissions.add(Permission.objects.get(codename="view_salary", content_type__app_label="employees"))
        self.client.force_authenticate(self.superuser)

        response = self.client.patch(self.detail_endpoint(self.regular_user.id), {
            "permissions": ["manage_devices"],
        }, format="json")

        self.assertEqual(response.status_code, 200)
        self.regular_user.refresh_from_db()
        self.assertFalse(self.regular_user.has_perm("employees.view_salary"))
        self.assertTrue(self.regular_user.has_perm("attendance.manage_devices"))

    def test_superuser_can_deactivate_and_reactivate_an_account(self):
        self.client.force_authenticate(self.superuser)

        response = self.client.patch(self.detail_endpoint(self.regular_user.id), {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.regular_user.refresh_from_db()
        self.assertFalse(self.regular_user.is_active)

        response = self.client.patch(self.detail_endpoint(self.regular_user.id), {"is_active": True}, format="json")
        self.regular_user.refresh_from_db()
        self.assertTrue(self.regular_user.is_active)

    def test_superuser_can_reset_a_password(self):
        self.client.force_authenticate(self.superuser)

        response = self.client.patch(self.detail_endpoint(self.regular_user.id), {"reset_password": True}, format="json")

        self.assertEqual(response.status_code, 200)
        self.regular_user.refresh_from_db()
        self.assertTrue(self.regular_user.check_password(response.data["temporary_password"]))
        self.assertFalse(self.regular_user.check_password("password"))

    def test_superuser_accounts_cannot_be_edited_through_this_endpoint(self):
        other_superuser = get_user_model().objects.create_superuser(
            username="other-root", email="other@example.com", password="password",
        )
        self.client.force_authenticate(self.superuser)

        response = self.client.patch(self.detail_endpoint(other_superuser.id), {"is_active": False}, format="json")

        self.assertEqual(response.status_code, 400)
        other_superuser.refresh_from_db()
        self.assertTrue(other_superuser.is_active)

    def test_updating_an_unknown_account_returns_404(self):
        self.client.force_authenticate(self.superuser)
        response = self.client.patch(self.detail_endpoint(999999), {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 404)
