from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

from core.models import must_change_password

# The only things a person on a temporary password may do: see who they are, change the password, sign out.
ALLOWED_WHILE_PASSWORD_CHANGE_PENDING = {
    "/api/auth/me/",
    "/api/auth/change-password/",
    "/api/auth/logout/",
    "/api/auth/refresh/",
}


class PasswordChangeRequired(PermissionDenied):
    # A dict body so the code reaches the browser: DRF's JSON output carries only the message, not ErrorDetail codes.
    default_detail = {
        "detail": "You must change your temporary password before you can continue.",
        "code": "password_change_required",
    }


class ForcedPasswordChangeJWTAuthentication(JWTAuthentication):
    """JWT login that refuses every API call (except the few above) until a temporary password has been replaced.
    Enforced here, on the server, so it cannot be skipped by going straight to a page or an API URL."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, _token = result
        if request.path not in ALLOWED_WHILE_PASSWORD_CHANGE_PENDING and must_change_password(user):
            raise PasswordChangeRequired()
        return result
