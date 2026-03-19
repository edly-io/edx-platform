"""
Helper functions for Two Factor Authentication.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from openedx.features.fbr_features.two_factor_auth.models import EmailOTP

log = logging.getLogger(__name__)

OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 10
OTP_SESSION_EXPIRY_SECONDS = OTP_EXPIRY_MINUTES * 60
OTP_CREATION_DEDUP_SECONDS = 30

SESSION_KEY_USER_ID = '_2fa_pending_user_id'
SESSION_KEY_EXPIRES = '_2fa_pending_expires'
SESSION_KEY_NEXT_URL = '_2fa_next_url'
SESSION_KEY_FINISH_AUTH_URL = '_2fa_finish_auth_url'


def _hash_otp(otp):
    return hashlib.sha256(otp.encode('utf-8')).hexdigest()


def generate_otp():
    return str(secrets.randbelow(10 ** OTP_LENGTH)).zfill(OTP_LENGTH)


def create_otp_for_user(user):
    """
    Create a new OTP for the user unless one was already created within the
    deduplication window.

    Concurrent login requests (e.g. from a rapid double-click) all reach this
    function at roughly the same time.  The SELECT FOR UPDATE inside the
    transaction ensures only the first request creates an OTP; the rest find
    the freshly-created record and return None so no duplicate is sent.

    Returns the plaintext OTP when a new one is created, or None when an
    existing active OTP is still within the deduplication window.
    """
    with transaction.atomic():
        recent_cutoff = timezone.now() - timedelta(seconds=OTP_CREATION_DEDUP_SECONDS)
        existing = (
            EmailOTP.objects
            .select_for_update()
            .filter(
                user=user,
                is_used=False,
                expires_at__gt=timezone.now(),
                created_at__gte=recent_cutoff,
            )
            .first()
        )
        if existing:
            return None

        EmailOTP.objects.filter(user=user, is_used=False).update(is_used=True)
        otp = generate_otp()
        EmailOTP.objects.create(
            user=user,
            otp_hash=_hash_otp(otp),
            expires_at=timezone.now() + timedelta(minutes=OTP_EXPIRY_MINUTES),
        )
    return otp


def send_otp_to_user(user, otp):
    """
    Deliver the OTP to the user.

    When DEBUG or ENABLE_2FA_EMAIL_LOG is True, the OTP is logged instead of
    emailed so local development works without a configured email backend.
    """
    if settings.DEBUG or settings.ENABLE_2FA_EMAIL_LOG:
        log.warning(
            '[2FA] OTP for user %s (%s): %s  (expires in %d minutes)',
            user.username,
            user.email,
            otp,
            OTP_EXPIRY_MINUTES,
        )
        return

    try:
        display_name = user.profile.name or user.username
    except AttributeError:
        display_name = user.username

    message = (
        f'Hi {display_name},\n\n'
        f'Your verification code is: {otp}\n\n'
        f'This code expires in {OTP_EXPIRY_MINUTES} minutes.\n\n'
        f'If you did not request this, please ignore this email or contact support.\n'
    )

    send_mail(
        subject='Your login verification code',
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    log.info('[2FA] OTP email sent to user %s', user.username)


def generate_and_send_otp(user):
    otp = create_otp_for_user(user)
    if otp is not None:
        send_otp_to_user(user, otp)


def verify_otp_for_user(user, submitted_otp):
    """
    Verify the submitted OTP against the latest valid (unused, unexpired) OTP for the user.

    Uses an atomic update to prevent concurrent requests from both succeeding with the
    same OTP. Returns True on success, False otherwise.
    """
    submitted_hash = _hash_otp(submitted_otp)

    otp_obj = (
        EmailOTP.objects
        .filter(user=user, is_used=False, expires_at__gt=timezone.now())
        .order_by('-created_at')
        .first()
    )

    if otp_obj is None:
        log.warning('[2FA] No valid OTP found for user %s', user.username)
        return False

    if not hmac.compare_digest(otp_obj.otp_hash, submitted_hash):
        log.warning('[2FA] Invalid OTP submitted for user %s', user.username)
        return False

    updated = EmailOTP.objects.filter(pk=otp_obj.pk, is_used=False).update(is_used=True)
    if not updated:
        log.warning('[2FA] OTP already consumed (concurrent attempt) for user %s', user.username)
        return False

    log.info('[2FA] OTP verified successfully for user %s', user.username)
    return True


def is_2fa_enabled_for_user():
    return bool(settings.ENABLE_2FA)
