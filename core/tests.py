from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
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
            {"id", "username", "is_superuser", "must_change_password", "permissions"},
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


class RoleGroupPermissionTests(TestCase):
    """testuser01 kept seeing payroll after it was unticked in Settings: payroll came
    from the 'Payroll Officer' role group as well as a direct grant, and Settings could
    only ever change the direct one."""

    def setUp(self):
        self.client = APIClient()
        self.admin = get_user_model().objects.create_superuser("root-admin", password="password")
        self.client.force_authenticate(self.admin)
        self.officer_group = Group.objects.create(name="Payroll Officer")
        self.officer_group.permissions.add(Permission.objects.get(codename="view_payroll"), Permission.objects.get(codename="manage_payroll"))
        self.user = get_user_model().objects.create_user("testuser01", password="password")
        self.user.groups.add(self.officer_group)
        self.user.user_permissions.add(Permission.objects.get(codename="view_payroll"))

    def account(self):
        rows = self.client.get("/api/auth/users/").json()["results"]
        return next(row for row in rows if row["username"] == "testuser01")

    def test_the_account_says_where_each_permission_comes_from(self):
        account = self.account()

        self.assertEqual(account["groups"], ["Payroll Officer"])
        self.assertTrue(account["direct_permissions"]["view_payroll"])
        self.assertFalse(account["direct_permissions"]["manage_payroll"])
        self.assertEqual(account["inherited_permissions"]["view_payroll"], ["Payroll Officer"])
        self.assertEqual(account["inherited_permissions"]["manage_payroll"], ["Payroll Officer"])
        self.assertTrue(account["permissions"]["manage_payroll"])

    def test_the_list_offers_the_available_role_groups(self):
        self.assertIn("Payroll Officer", self.client.get("/api/auth/users/").json()["available_groups"])

    def test_clearing_direct_permissions_alone_leaves_payroll_access_through_the_role(self):
        response = self.client.patch(f"/api/auth/users/{self.user.pk}/", {"permissions": []}, format="json")

        self.assertEqual(response.status_code, 200)
        account = response.json()["account"]
        self.assertFalse(account["direct_permissions"]["view_payroll"])
        self.assertTrue(account["permissions"]["view_payroll"])
        self.assertTrue(account["permissions"]["manage_payroll"])

    def test_removing_the_role_and_the_direct_grant_takes_payroll_access_away(self):
        response = self.client.patch(f"/api/auth/users/{self.user.pk}/", {"permissions": [], "groups": []}, format="json")

        account = response.json()["account"]
        self.assertEqual(account["groups"], [])
        self.assertFalse(account["permissions"]["view_payroll"])
        self.assertFalse(account["permissions"]["manage_payroll"])
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/payroll/periods/").status_code, 403)

    def test_unknown_role_groups_are_rejected_and_change_nothing(self):
        response = self.client.patch(f"/api/auth/users/{self.user.pk}/", {"groups": ["Payroll Officer", "Nonexistent Role"]}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Nonexistent Role", response.json()["detail"])
        self.assertEqual(list(self.user.groups.values_list("name", flat=True)), ["Payroll Officer"])

    def test_omitting_groups_leaves_role_membership_untouched(self):
        self.client.patch(f"/api/auth/users/{self.user.pk}/", {"permissions": []}, format="json")
        self.assertEqual(list(self.user.groups.values_list("name", flat=True)), ["Payroll Officer"])



class ChangePasswordTests(TestCase):
    endpoint = "/api/auth/change-password/"

    def setUp(self):
        from rest_framework.test import APIClient

        self.user = get_user_model().objects.create_user(username="pw-user", password="Temporary-Pass-482")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def change(self, current, new):
        return self.client.post(self.endpoint, {"current_password": current, "new_password": new}, format="json")

    def test_a_user_can_change_their_own_password(self):
        response = self.change("Temporary-Pass-482", "Brand-New-Secret-731")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Brand-New-Secret-731"))

    def test_the_wrong_current_password_is_refused(self):
        response = self.change("not-it", "Brand-New-Secret-731")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Temporary-Pass-482"))

    def test_a_weak_or_unchanged_password_is_refused(self):
        self.assertEqual(self.change("Temporary-Pass-482", "12345678").status_code, 400)
        self.assertEqual(self.change("Temporary-Pass-482", "short").status_code, 400)
        self.assertEqual(self.change("Temporary-Pass-482", "Temporary-Pass-482").status_code, 400)

    def test_it_needs_a_signed_in_user(self):
        from rest_framework.test import APIClient

        self.assertEqual(APIClient().post(self.endpoint, {}, format="json").status_code, 401)


class ForcedPasswordChangeTests(TestCase):
    """A login that is still on its temporary password can only change it - the server refuses everything else."""

    def setUp(self):
        from core.models import set_must_change_password

        self.superuser = get_user_model().objects.create_superuser(username="fpc-admin", email="fpc@example.com", password="Admin-Secret-5521")
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.superuser)
        self.set_must_change_password = set_must_change_password

    def login(self, username, password):
        client = APIClient()
        response = client.post("/api/auth/login/", {"username": username, "password": password}, format="json")
        self.assertEqual(response.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return client

    def test_a_new_account_must_change_its_temporary_password_before_anything_else(self):
        created = self.admin_client.post("/api/auth/users/", {"username": "fpc-new", "permissions": []}, format="json")
        self.assertEqual(created.status_code, 201)
        client = self.login("fpc-new", created.data["temporary_password"])

        self.assertTrue(client.get("/api/auth/me/").data["must_change_password"])
        blocked = client.get("/api/notifications/unread-count/")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["code"], "password_change_required")

        changed = client.post("/api/auth/change-password/", {"current_password": created.data["temporary_password"], "new_password": "My-Own-Secret-7310"}, format="json")
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(client.get("/api/auth/me/").data["must_change_password"])
        self.assertEqual(client.get("/api/notifications/unread-count/").status_code, 200)

    def test_an_admin_reset_requires_a_new_change(self):
        user = get_user_model().objects.create_user(username="fpc-reset", password="Old-Secret-8842")
        reset = self.admin_client.patch(f"/api/auth/users/{user.pk}/", {"reset_password": True}, format="json")
        self.assertEqual(reset.status_code, 200)
        client = self.login("fpc-reset", reset.data["temporary_password"])
        self.assertEqual(client.get("/api/notifications/unread-count/").status_code, 403)

    def test_an_account_not_flagged_is_unaffected(self):
        get_user_model().objects.create_user(username="fpc-normal", password="Normal-Secret-3317")
        client = self.login("fpc-normal", "Normal-Secret-3317")
        self.assertFalse(client.get("/api/auth/me/").data["must_change_password"])
        self.assertEqual(client.get("/api/notifications/unread-count/").status_code, 200)

    def test_signing_out_is_still_possible_while_the_change_is_pending(self):
        user = get_user_model().objects.create_user(username="fpc-logout", password="Logout-Secret-6609")
        self.set_must_change_password(user, True)
        client = self.login("fpc-logout", "Logout-Secret-6609")
        self.assertNotEqual(client.post("/api/auth/logout/", {}, format="json").status_code, 403)


class BiometricsOverviewPermissionTests(TestCase):
    """Seeing the biometrics overview is its own permission, separate from managing the devices."""

    endpoint = "/api/attendance/biometrics-overview/"

    def get_as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(self.endpoint)

    def user_with(self, username, *codenames):
        user = get_user_model().objects.create_user(username=username, password="pw")
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        return user

    def test_someone_with_only_the_overview_permission_can_see_it(self):
        self.assertEqual(self.get_as(self.user_with("bio-viewer", "view_biometrics_overview")).status_code, 200)

    def test_a_device_manager_can_still_see_it(self):
        self.assertEqual(self.get_as(self.user_with("bio-manager", "manage_devices")).status_code, 200)

    def test_someone_with_neither_is_refused(self):
        self.assertEqual(self.get_as(self.user_with("bio-nobody")).status_code, 403)

    def test_the_overview_permission_does_not_let_you_manage_devices(self):
        user = self.user_with("bio-viewer-2", "view_biometrics_overview")
        client = APIClient()
        client.force_authenticate(user)
        response = client.post("/api/attendance/devices/", {"name": "x", "serial_number": "X1", "location": "y", "device_type": "face"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_both_permissions_are_offered_in_settings(self):
        self.assertIn("view_biometrics_overview", MANAGED_PERMISSION_CODENAMES)
        self.assertIn("manage_devices", MANAGED_PERMISSION_CODENAMES)
