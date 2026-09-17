import secrets
import string

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from core.permissions_registry import (
    MANAGED_PERMISSIONS,
    MANAGED_PERMISSION_CODENAMES,
    user_permission_map,
)


MEAL_PERMISSION_CODES = (
    "record_meal_operations",
    "review_meal_excess",
    "manage_meal_configuration",
)


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Login, rate-limited separately from every other endpoint."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class LogoutAPIView(APIView):
    """Blacklist the supplied refresh token so it can no longer be used."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response({"detail": "A refresh token is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            RefreshToken(refresh).blacklist()
        except TokenError:
            return Response({"detail": "Token is invalid or already blacklisted."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_205_RESET_CONTENT)


class CurrentUserAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(
            {
                "id": user.pk,
                "username": user.get_username(),
                "is_superuser": user.is_superuser,
                "permissions": {
                    # Legacy meals-only keys, kept so existing pages that
                    # read these directly keep working, plus every managed
                    # permission below.
                    **{code: user.has_perm(f"meals.{code}") for code in MEAL_PERMISSION_CODES},
                    **user_permission_map(user),
                },
            }
        )


class IsSuperUser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


def _generate_password():
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


def _serialize_account(user):
    return {
        "id": user.pk,
        "username": user.get_username(),
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "date_joined": user.date_joined,
        "last_login": user.last_login,
        "permissions": user_permission_map(user),
    }


class UserAccountListCreateAPIView(APIView):
    """Manage login accounts and which of the app's gated actions they can perform.

    Superuser-only: granting permissions is itself a sensitive action, and
    this app currently has no notion of "can manage other users" short of
    being the account that already implicitly has every permission.
    """

    permission_classes = [IsAuthenticated, IsSuperUser]

    def get(self, request):
        users = get_user_model().objects.order_by("username")
        return Response({
            "permission_registry": MANAGED_PERMISSIONS,
            "results": [_serialize_account(user) for user in users],
        })

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        if not username:
            return Response({"detail": "Username is required."}, status=status.HTTP_400_BAD_REQUEST)
        if get_user_model().objects.filter(username=username).exists():
            return Response({"detail": "That username is already taken."}, status=status.HTTP_400_BAD_REQUEST)

        requested = request.data.get("permissions") or []
        unknown = [code for code in requested if code not in MANAGED_PERMISSION_CODENAMES]
        if unknown:
            return Response({"detail": f"Unknown permission(s): {', '.join(unknown)}."}, status=status.HTTP_400_BAD_REQUEST)

        password = _generate_password()
        user = get_user_model().objects.create_user(username=username, password=password, is_staff=False)
        _apply_permissions(user, requested)

        return Response(
            {"account": _serialize_account(user), "temporary_password": password},
            status=status.HTTP_201_CREATED,
        )


class UserAccountDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsSuperUser]

    def patch(self, request, user_id):
        try:
            user = get_user_model().objects.get(pk=user_id)
        except get_user_model().DoesNotExist:
            return Response({"detail": "Account not found."}, status=status.HTTP_404_NOT_FOUND)

        if user.is_superuser:
            return Response(
                {"detail": "Superuser accounts already have every permission and can't be edited here."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if "is_active" in request.data:
            user.is_active = bool(request.data["is_active"])
            user.save(update_fields=["is_active"])

        if "permissions" in request.data:
            requested = request.data.get("permissions") or []
            unknown = [code for code in requested if code not in MANAGED_PERMISSION_CODENAMES]
            if unknown:
                return Response({"detail": f"Unknown permission(s): {', '.join(unknown)}."}, status=status.HTTP_400_BAD_REQUEST)
            _apply_permissions(user, requested)

        response_data = {"account": _serialize_account(user)}

        if request.data.get("reset_password"):
            new_password = _generate_password()
            user.set_password(new_password)
            user.save(update_fields=["password"])
            response_data["temporary_password"] = new_password

        return Response(response_data)


def _apply_permissions(user, codenames_to_grant):
    """Full replace: exactly the managed permissions named here end up granted, nothing else changes."""
    to_grant = set(codenames_to_grant)
    managed_permissions = Permission.objects.filter(codename__in=MANAGED_PERMISSION_CODENAMES)
    grant_ids = [permission.id for permission in managed_permissions if permission.codename in to_grant]
    revoke_ids = [permission.id for permission in managed_permissions if permission.codename not in to_grant]
    if revoke_ids:
        user.user_permissions.remove(*revoke_ids)
    if grant_ids:
        user.user_permissions.add(*grant_ids)
