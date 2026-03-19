"""
Tests for Two Factor Authentication API views.
"""

import time
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from common.djangoapps.student.tests.factories import TEST_PASSWORD, UserFactory
from openedx.features.fbr_features.two_factor_auth.models import EmailOTP
from openedx.features.fbr_features.two_factor_auth.utils import (
    SESSION_KEY_EXPIRES,
    SESSION_KEY_FINISH_AUTH_URL,
    SESSION_KEY_NEXT_URL,
    SESSION_KEY_USER_ID,
    _hash_otp,
)

VERIFY_URL = '/api/2fa/v1/verify-login/'
RESEND_URL = '/api/2fa/v1/resend/'


class TwoFactorViewTestMixin:
    """Common helpers shared between 2FA view test classes."""

    def _seed_session(self, user, expired=False):
        session = self.client.session
        session[SESSION_KEY_USER_ID] = user.id
        session[SESSION_KEY_EXPIRES] = time.time() + (-1 if expired else 600)
        session.save()

    def _create_otp(self, user, code='123456', used=False, minutes_offset=10):
        return EmailOTP.objects.create(
            user=user,
            otp_hash=_hash_otp(code),
            expires_at=timezone.now() + timedelta(minutes=minutes_offset),
            is_used=used,
        )


class VerifyLoginOTPViewTests(TwoFactorViewTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    # --- session guard ---

    def test_no_session_returns_401(self):
        response = self.client.post(VERIFY_URL, {'otp': '123456'})
        self.assertEqual(response.status_code, 401)

    def test_expired_session_returns_401(self):
        self._seed_session(self.user, expired=True)
        response = self.client.post(VERIFY_URL, {'otp': '123456'})
        self.assertEqual(response.status_code, 401)

    def test_inactive_user_returns_401(self):
        inactive_user = UserFactory.create(is_active=False)
        self._seed_session(inactive_user)
        response = self.client.post(VERIFY_URL, {'otp': '123456'})
        self.assertEqual(response.status_code, 401)

    # --- input validation ---

    def test_missing_otp_returns_400(self):
        self._seed_session(self.user)
        response = self.client.post(VERIFY_URL, {})
        self.assertEqual(response.status_code, 400)

    # --- OTP verification ---

    def test_wrong_otp_returns_400(self):
        self._seed_session(self.user)
        self._create_otp(self.user, '123456')
        response = self.client.post(VERIFY_URL, {'otp': '000000'})
        self.assertEqual(response.status_code, 400)

    def test_expired_otp_returns_400(self):
        self._seed_session(self.user)
        self._create_otp(self.user, '123456', minutes_offset=-1)
        response = self.client.post(VERIFY_URL, {'otp': '123456'})
        self.assertEqual(response.status_code, 400)

    @patch('openedx.features.fbr_features.two_factor_auth.views._handle_successful_authentication_and_login')
    @patch('openedx.features.fbr_features.two_factor_auth.views.set_logged_in_cookies', side_effect=lambda req, resp, user: resp)
    def test_valid_otp_returns_success_with_redirect(self, _mock_cookies, _mock_login):
        self._seed_session(self.user)
        self._create_otp(self.user, '123456')
        response = self.client.post(VERIFY_URL, {'otp': '123456'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('redirect_url', data)

    @patch('openedx.features.fbr_features.two_factor_auth.views._handle_successful_authentication_and_login')
    @patch('openedx.features.fbr_features.two_factor_auth.views.set_logged_in_cookies', side_effect=lambda req, resp, user: resp)
    def test_valid_otp_clears_2fa_session_keys(self, _mock_cookies, _mock_login):
        self._seed_session(self.user)
        self._create_otp(self.user, '123456')
        self.client.post(VERIFY_URL, {'otp': '123456'})
        session = self.client.session
        self.assertNotIn(SESSION_KEY_USER_ID, session)
        self.assertNotIn(SESSION_KEY_EXPIRES, session)

    @patch('openedx.features.fbr_features.two_factor_auth.views._handle_successful_authentication_and_login')
    @patch('openedx.features.fbr_features.two_factor_auth.views.set_logged_in_cookies', side_effect=lambda req, resp, user: resp)
    def test_redirect_uses_next_url(self, _mock_cookies, _mock_login):
        session = self.client.session
        session[SESSION_KEY_USER_ID] = self.user.id
        session[SESSION_KEY_EXPIRES] = time.time() + 600
        session[SESSION_KEY_NEXT_URL] = '/courses/'
        session.save()
        self._create_otp(self.user, '123456')
        response = self.client.post(VERIFY_URL, {'otp': '123456'})
        self.assertIn('/courses/', response.json()['redirect_url'])

    @patch('openedx.features.fbr_features.two_factor_auth.views._handle_successful_authentication_and_login')
    @patch('openedx.features.fbr_features.two_factor_auth.views.set_logged_in_cookies', side_effect=lambda req, resp, user: resp)
    def test_non_slash_relative_redirect_falls_back_to_dashboard(self, _mock_cookies, _mock_login):
        session = self.client.session
        session[SESSION_KEY_USER_ID] = self.user.id
        session[SESSION_KEY_EXPIRES] = time.time() + 600
        session[SESSION_KEY_NEXT_URL] = 'javascript:alert(1)'
        session.save()
        self._create_otp(self.user, '123456')
        response = self.client.post(VERIFY_URL, {'otp': '123456'})
        self.assertIn('/dashboard', response.json()['redirect_url'])


class ResendOTPViewTests(TwoFactorViewTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def test_no_session_returns_401(self):
        response = self.client.post(RESEND_URL, {})
        self.assertEqual(response.status_code, 401)

    def test_expired_session_returns_401(self):
        self._seed_session(self.user, expired=True)
        response = self.client.post(RESEND_URL, {})
        self.assertEqual(response.status_code, 401)

    @patch('openedx.features.fbr_features.two_factor_auth.views.generate_and_send_otp')
    def test_valid_session_triggers_otp_and_returns_200(self, mock_send):
        self._seed_session(self.user)
        response = self.client.post(RESEND_URL, {})
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once_with(self.user)
