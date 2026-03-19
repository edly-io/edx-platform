"""
URL configuration for Two Factor Authentication API.

All routes are prefixed with /api/2fa/v1/ from the root URL conf.
"""

from django.urls import path

from .views import ResendOTPView, VerifyLoginOTPView

urlpatterns = [
    path('verify-login/', VerifyLoginOTPView.as_view(), name='2fa_verify_login'),
    path('resend/', ResendOTPView.as_view(), name='2fa_resend'),
]
