from django.core.cache import cache
from django.contrib.auth import get_user_model
from unittest import mock

from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle


class LoginThrottleTests(APITestCase):
    """2026-09-25 review: rotating X-Forwarded-For dodged the 5/minute login throttle."""

    url = "/api/auth/login/"

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_user(username="ada", password="right-password")

    def attempt(self, username, forwarded, remote="10.0.0.9"):
        return self.client.post(self.url, {"username": username, "password": "wrong"}, format="json", HTTP_X_FORWARDED_FOR=forwarded, REMOTE_ADDR=remote)

    def test_a_client_cannot_dodge_the_limit_by_sending_its_own_forwarded_for(self):
        # nginx appends the real address last; everything before it is whatever the client claimed.
        codes = [self.attempt("ada", f"spoof-{n}, 203.0.113.7").status_code for n in range(8)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(set(codes[5:]), {429})

    def test_a_different_real_address_has_its_own_allowance(self):
        for n in range(6):
            self.attempt("ada", f"spoof-{n}, 203.0.113.7")
        self.assertEqual(self.attempt("ada", "anything, 198.51.100.4").status_code, 401)

    def test_one_account_is_limited_whatever_address_the_attempts_come_from(self):
        # DRF reads the rates into a class attribute at import time, so patch that rather than the setting.
        with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", {"login": "1000/min", "login_user": "3/hour"}):
            codes = [self.attempt("Ada", f"x, 198.51.100.{n + 1}").status_code for n in range(5)]
        self.assertEqual(codes[:3], [401] * 3)
        self.assertEqual(set(codes[3:]), {429})
