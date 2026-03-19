"""
Models for Two Factor Authentication.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class EmailOTP(models.Model):
    """
    Stores a one-time password sent to the user's email for 2FA login verification.

    A new OTP is created on each login attempt. All previous unused OTPs for the
    same user are invalidated when a new one is generated.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='email_otps',
        db_index=True,
    )
    otp_hash = models.CharField(
        max_length=64,
        help_text='SHA-256 hash of the 6-digit OTP code.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text='OTP is invalid after this time.',
    )
    is_used = models.BooleanField(
        default=False,
        help_text='True once the OTP has been successfully verified.',
    )

    class Meta:
        app_label = 'two_factor_auth'
        verbose_name = 'Email OTP'
        verbose_name_plural = 'Email OTPs'
        ordering = ['-created_at']

    def __str__(self):
        return f'EmailOTP for {self.user.email} (used={self.is_used}, expires={self.expires_at})'

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at


