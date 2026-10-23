from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.throttling import BaseThrottle

from .models import IntegrationKey, hash_key


class IntegrationKeyAuthentication(BaseAuthentication):
    """`Authorization: Api-Key <key>` (or `X-Api-Key: <key>`). Authenticates the outside system, not a person."""

    keyword = "Api-Key"

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        raw = header[len(self.keyword) + 1:].strip() if header.startswith(self.keyword + " ") else request.META.get("HTTP_X_API_KEY", "").strip()
        if not raw:
            return None
        key = IntegrationKey.objects.filter(key_hash=hash_key(raw)).first()
        if key is None or not key.active:
            raise AuthenticationFailed("This API key is not valid or has been switched off.")
        ip = BaseThrottle().get_ident(request)  # the caller's address, behind the proxy (REST_FRAMEWORK NUM_PROXIES)
        if not key.ip_allowed(ip):
            raise AuthenticationFailed("This API key cannot be used from this address.")
        IntegrationKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now(), last_used_ip=ip[:64], request_count=key.request_count + 1)
        return (None, key)

    def authenticate_header(self, request):
        return self.keyword


class HasIntegrationKey(BasePermission):
    message = "Send a valid API key: Authorization: Api-Key <key>."

    def has_permission(self, request, view):
        return isinstance(request.auth, IntegrationKey)
