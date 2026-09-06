from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView


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
                    code: user.has_perm(f"meals.{code}")
                    for code in MEAL_PERMISSION_CODES
                },
            }
        )
