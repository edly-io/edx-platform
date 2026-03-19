"""
Two Factor Authentication App Configuration
"""

from django.apps import AppConfig


class TwoFactorAuthConfig(AppConfig):
    """
    Application Configuration for Two Factor Authentication.
    """
    name = 'openedx.features.fbr_features.two_factor_auth'
    verbose_name = 'Two Factor Authentication'
