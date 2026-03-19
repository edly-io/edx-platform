"""
API views for Two Factor Authentication.
"""

import logging
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.http import url_has_allowed_host_and_scheme
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from openedx.core.djangoapps.user_authn.cookies import set_logged_in_cookies
from openedx.core.djangoapps.user_authn.views.login import _handle_successful_authentication_and_login

from .utils import (
    SESSION_KEY_EXPIRES,
    SESSION_KEY_FINISH_AUTH_URL,
    SESSION_KEY_NEXT_URL,
    SESSION_KEY_USER_ID,
    generate_and_send_otp,
    verify_otp_for_user,
)

log = logging.getLogger(__name__)
User = get_user_model()


class TwoFactorVerifyThrottle(AnonRateThrottle):
    """
    Throttle OTP verify attempts to reduce brute-force and burst traffic.
    """
    rate = settings.TWO_FACTOR_VERIFY_API_RATELIMIT


class TwoFactorResendThrottle(AnonRateThrottle):
    """
    Throttle OTP resend attempts to reduce OTP generation bursts.
    """
    rate = settings.TWO_FACTOR_RESEND_API_RATELIMIT


def _get_pending_user(request):
    """
    Retrieve the user awaiting 2FA verification from the session.

    Returns (user, error_response). On success user is set and error_response is None.
    On failure user is None and error_response contains the appropriate Response.
    """
    user_id = request.session.get(SESSION_KEY_USER_ID)
    expires_at = request.session.get(SESSION_KEY_EXPIRES)

    if not user_id or not expires_at:
        return None, Response(
            {'error': '2FA session not found. Please log in again.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if time.time() > expires_at:
        _clear_2fa_session(request)
        return None, Response(
            {'error': '2FA session expired. Please log in again.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        _clear_2fa_session(request)
        return None, Response(
            {'error': 'User not found.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        _clear_2fa_session(request)
        return None, Response(
            {'error': 'This account is disabled.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    return user, None


def _clear_2fa_session(request):
    request.session.pop(SESSION_KEY_USER_ID, None)
    request.session.pop(SESSION_KEY_EXPIRES, None)
    request.session.pop(SESSION_KEY_FINISH_AUTH_URL, None)
    request.session.pop(SESSION_KEY_NEXT_URL, None)


class VerifyLoginOTPView(APIView):
    """
    Verify the OTP submitted after password authentication and complete the login.

    POST /api/2fa/v1/verify-login/
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [TwoFactorVerifyThrottle]

    def post(self, request):
        user, err = _get_pending_user(request)
        if err:
            return err

        otp = request.data.get('otp', '').strip()
        if not otp:
            return Response({'error': 'OTP is required.'}, status=status.HTTP_400_BAD_REQUEST)

        if not verify_otp_for_user(user, otp):
            return Response(
                {'error': 'Invalid or expired OTP. Please try again.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        finish_auth_url = request.session.get(SESSION_KEY_FINISH_AUTH_URL)
        next_url = request.session.get(SESSION_KEY_NEXT_URL)

        if not hasattr(user, 'backend'):
            user.backend = 'django.contrib.auth.backends.AllowAllUsersModelBackend'

        _handle_successful_authentication_and_login(user, request)
        _clear_2fa_session(request)

        lms_root_url = settings.LMS_ROOT_URL.rstrip('/')
        raw_url = finish_auth_url or next_url or '/dashboard'

        if raw_url.startswith('http'):
            allowed = url_has_allowed_host_and_scheme(
                url=raw_url,
                allowed_hosts={request.get_host(), settings.LMS_BASE},
                require_https=settings.HTTPS == 'on',
            )
            redirect_url = raw_url if allowed else lms_root_url + '/dashboard'
        elif raw_url.startswith('/'):
            redirect_url = lms_root_url + raw_url
        else:
            redirect_url = lms_root_url + '/dashboard'

        response = Response({'success': True, 'redirect_url': redirect_url})
        return set_logged_in_cookies(request, response, user)


class ResendOTPView(APIView):
    """
    Regenerate and resend the OTP for the pending 2FA session.

    POST /api/2fa/v1/resend/
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [TwoFactorResendThrottle]

    def post(self, request):
        user, err = _get_pending_user(request)
        if err:
            return err

        generate_and_send_otp(user)
        return Response({'message': 'If a new code was needed, it has been sent to your email.'})
