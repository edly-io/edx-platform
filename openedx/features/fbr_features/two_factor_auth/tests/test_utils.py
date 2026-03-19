"""
Tests for Two Factor Authentication utility functions.
"""

import hashlib
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from common.djangoapps.student.tests.factories import UserFactory
from openedx.features.fbr_features.two_factor_auth.models import EmailOTP
from openedx.features.fbr_features.two_factor_auth.utils import (
    OTP_CREATION_DEDUP_SECONDS,
    OTP_LENGTH,
    create_otp_for_user,
    generate_otp,
    is_2fa_enabled_for_user,
    verify_otp_for_user,
)


def _hash(otp):
    return hashlib.sha256(otp.encode('utf-8')).hexdigest()


class GenerateOTPTests(TestCase):

    def test_length(self):
        self.assertEqual(len(generate_otp()), OTP_LENGTH)

    def test_numeric(self):
        self.assertTrue(generate_otp().isdigit())

    def test_zero_padded(self):
        with patch('openedx.features.fbr_features.two_factor_auth.utils.secrets.randbelow', return_value=0):
            self.assertEqual(generate_otp(), '0' * OTP_LENGTH)


class CreateOTPForUserTests(TestCase):

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def test_creates_otp_record(self):
        otp = create_otp_for_user(self.user)
        self.assertIsNotNone(otp)
        self.assertEqual(EmailOTP.objects.filter(user=self.user, is_used=False).count(), 1)

    def test_stores_hash_not_plaintext(self):
        otp = create_otp_for_user(self.user)
        record = EmailOTP.objects.get(user=self.user, is_used=False)
        self.assertEqual(record.otp_hash, _hash(otp))
        self.assertNotEqual(record.otp_hash, otp)

    def test_invalidates_previous_otp_on_new_creation(self):
        create_otp_for_user(self.user)
        # Move the existing OTP outside the dedup window so a new one is created
        EmailOTP.objects.filter(user=self.user).update(
            created_at=timezone.now() - timedelta(seconds=OTP_CREATION_DEDUP_SECONDS + 1)
        )
        create_otp_for_user(self.user)

        self.assertEqual(EmailOTP.objects.filter(user=self.user, is_used=False).count(), 1)
        self.assertEqual(EmailOTP.objects.filter(user=self.user, is_used=True).count(), 1)

    def test_dedup_returns_none_within_window(self):
        create_otp_for_user(self.user)
        self.assertIsNone(create_otp_for_user(self.user))

    def test_dedup_allows_new_otp_after_window(self):
        create_otp_for_user(self.user)
        EmailOTP.objects.filter(user=self.user).update(
            created_at=timezone.now() - timedelta(seconds=OTP_CREATION_DEDUP_SECONDS + 1)
        )
        self.assertIsNotNone(create_otp_for_user(self.user))


class VerifyOTPForUserTests(TestCase):

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def _create_valid_otp(self, code='123456'):
        return EmailOTP.objects.create(
            user=self.user,
            otp_hash=_hash(code),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    def test_valid_otp_returns_true(self):
        self._create_valid_otp()
        self.assertTrue(verify_otp_for_user(self.user, '123456'))

    def test_valid_otp_marks_record_as_used(self):
        self._create_valid_otp()
        verify_otp_for_user(self.user, '123456')
        self.assertTrue(EmailOTP.objects.get(user=self.user).is_used)

    def test_wrong_otp_returns_false(self):
        self._create_valid_otp('123456')
        self.assertFalse(verify_otp_for_user(self.user, '000000'))

    def test_expired_otp_returns_false(self):
        EmailOTP.objects.create(
            user=self.user,
            otp_hash=_hash('123456'),
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(verify_otp_for_user(self.user, '123456'))

    def test_already_used_otp_returns_false(self):
        EmailOTP.objects.create(
            user=self.user,
            otp_hash=_hash('123456'),
            expires_at=timezone.now() + timedelta(minutes=10),
            is_used=True,
        )
        self.assertFalse(verify_otp_for_user(self.user, '123456'))

    def test_no_otp_exists_returns_false(self):
        self.assertFalse(verify_otp_for_user(self.user, '123456'))

    def test_replay_attack_returns_false(self):
        self._create_valid_otp()
        verify_otp_for_user(self.user, '123456')
        self.assertFalse(verify_otp_for_user(self.user, '123456'))


class Is2FAEnabledTests(TestCase):

    @patch('openedx.features.fbr_features.two_factor_auth.utils.settings')
    def test_returns_true_when_enabled(self, mock_settings):
        mock_settings.ENABLE_2FA = True
        self.assertTrue(is_2fa_enabled_for_user())

    @patch('openedx.features.fbr_features.two_factor_auth.utils.settings')
    def test_returns_false_when_disabled(self, mock_settings):
        mock_settings.ENABLE_2FA = False
        self.assertFalse(is_2fa_enabled_for_user())
