"""
Views related to the Edly app feature.
"""
import logging
from datetime import timedelta
from django.contrib.auth.views import redirect_to_login
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.utils.translation import ugettext as _
from django.conf import settings

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.edly.models import OTPSession, TwoFactorBypass
from openedx.features.edly.utils import send_otp_email

log = logging.getLogger(__name__)


def account_deactivated_view(request):
    return render(request=request, template_name="account_deactivated.html")


def should_enforce_two_fa(user=None):
    """Check if two-factor authentication should be enforced for a user."""
    if not getattr(settings, 'TWO_FA_ENFORCE', False):
        return False
    
    if not user:
        return True

    is_superuser_bypass = (
        getattr(settings, 'TWO_FA_ENFORCE_BYPASS_SUPERUSER', False) 
        and user.is_superuser
    )
    has_bypass = TwoFactorBypass.objects.filter(user=user).exists()
    
    return not (is_superuser_bypass or has_bypass)


def handle_redirect_with_2fa(redirect_url, request):
    """Store redirect URL in session and return OTP verification URL."""
    request.session['2fa_redirect_url'] = redirect_url
    request.session.save()
    return reverse('edly_app_urls:verify_otp')


def handle_otp_flow(possibly_authenticated_user, request, redirect_url=None):
    """Handle OTP flow initialization for a user."""
    if not possibly_authenticated_user or not request.session.session_key:
        log.error("Missing user or session key in handle_otp_flow")
        return redirect_to_login('/')

    try:
        otp_session = _create_or_get_otp_session(
            possibly_authenticated_user, 
            request.session.session_key,
            request
        )
        _setup_2fa_session(request, possibly_authenticated_user, otp_session)
        return HttpResponseRedirect(reverse('edly_app_urls:verify_otp'))

    except Exception as e:
        log.error(
            "Error initiating 2FA for user %s: %s",
            getattr(possibly_authenticated_user, 'id', 'Unknown'),
            str(e)
        )
        messages.error(
            request,
            "Error setting up two-factor authentication. Please try again."
        )
        return redirect_to_login('/')


def _create_or_get_otp_session(user, session_key, request):
    """Create or get OTP session and generate new OTP code."""
    otp_session, created = OTPSession.objects.get_or_create(
        user=user,
        session_key=session_key,
        defaults={'otp_code': '', 'expires_at': timezone.now()}
    )
    
    if created:
        otp_code = otp_session.generate_otp()
        send_otp_email(otp_session.user, request.site, otp_code)
    return otp_session


def _setup_2fa_session(request, user, otp_session):
    """Set up 2FA session variables."""
    session_data = {
        '2fa_user_backend': user.backend,
        '2fa_user_id': user.id,
        '2fa_user_email': user.email,
        '2fa_required': True,
        '2fa_session_id': otp_session.id,
        '2fa_initiated': True
    }
    
    for key, value in session_data.items():
        request.session[key] = value
    
    request.session.save()


def cancel_two_factor_auth(request):
    """
    Cancel two-factor authentication and redirect to login.
    """
    next_url = request.session.get('2fa_redirect_url', '/')
    _flush_2fa_session_variables(request)
    return redirect_to_login(next_url)


@csrf_protect
def verify_otp_view(request):
    """
    View for OTP verification - handles both GET (render page) and POST (verify code).
    """
    if hasattr(request, 'user') and getattr(request.user, 'is_authenticated', False):
        next_url = request.session.get('2fa_redirect_url', '/dashboard')
        return redirect_to_login(next_url)

    if not request.session.get('2fa_required', False):
        return redirect('/')
    if request.method == 'POST':
        return _handle_form_otp_verification(request)
    
    return _render_otp_page(request)



@csrf_protect
@require_http_methods(["POST"])
def resend_otp_view(request):
    """
    Resend OTP code via email with rate limiting.
    """
    if not request.session.get('2fa_required', False):
        return redirect('/')

    try:
        otp_session_id = request.session.get('2fa_session_id')
        user_id = request.session.get('2fa_user_id')
        if not otp_session_id or not user_id:
            return JsonResponse({'success': False, 'message': 'Invalid session'})

        otp_session = OTPSession.objects.get(
            id=otp_session_id,
            user__id=user_id,
            is_verified=False
        )

        resend_cooldown = _get_resend_cooldown_remaining(otp_session)
        if resend_cooldown:
            return JsonResponse({
                'success': False,
                'message': _('You can only request the next OTP in {}').format(resend_cooldown)
            })

        otp_code = otp_session.generate_otp()
        otp_session.attempts = 0
        otp_session.created_at = timezone.now()
        otp_session.save()

        send_otp_email(otp_session.user, request.site, otp_code)

        return JsonResponse({
            'success': True,
            'message': _('Verification code sent successfully!')
        })

    except OTPSession.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': _('Invalid OTP session. Please login again.')
        })
    except Exception as e:
        log.error(f"Error resending OTP for user {user_id}: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': _('An error occurred. Please try again.')
        })


def _get_resend_cooldown_remaining(otp_session):
    """
    Calculate remaining time before user can request another OTP.
    Returns formatted time string or None if cooldown has expired.
    """
    cooldown_seconds = getattr(settings, 'TWO_FA_RESEND_COOLDOWN_SECONDS', 180)
    cooldown_period = timedelta(seconds=cooldown_seconds)
    
    time_diff = timezone.now() - otp_session.created_at
    
    if time_diff < cooldown_period:
        remaining_time = cooldown_period - time_diff
        remaining_seconds = int(remaining_time.total_seconds())
        
        if remaining_seconds >= 60:
            remaining_minutes = remaining_seconds // 60
            remaining_seconds = remaining_seconds % 60
            return f"{remaining_minutes} minute{'s' if remaining_minutes != 1 else ''} and {remaining_seconds} second{'s' if remaining_seconds != 1 else ''}"
        else:
            return f"{remaining_seconds} second{'s' if remaining_seconds != 1 else ''}"
    
    return None


def _handle_form_otp_verification(request):
    """Handle regular form OTP verification."""
    otp_code = request.POST.get('otp_code', '').strip()
    
    try:
        otp_session = _get_otp_session(request)
        if not otp_session:
            messages.error(request, "Invalid session. Please login again.")
            return redirect('/login')
        
        otp_session.attempts += 1
        otp_session.save()

        validation_result = _validate_otp_session(otp_session)
        if validation_result:
            messages.error(request, validation_result)
            return _render_otp_page(request)

        if otp_session.otp_code == otp_code:
            # To avoid circular imports
            from openedx.core.djangoapps.user_authn.views.login import (
                handle_successful_authentication_and_login,
                handle_successful_login,
            )
            user = otp_session.user
            user.backend = request.session.get('2fa_user_backend')
            _mark_otp_verified(request, otp_session)
            handle_successful_authentication_and_login(user, request)
            redirect_url = request.session.get('2fa_redirect_url', '/')
            response = handle_successful_login(user, request, redirect_url, redirect(redirect_url))
            _flush_2fa_session_variables(request)
            return response
        else:
            max_attempts = getattr(settings, 'TWO_FA_ATTEMPTS_MAX_LIMIT', 3)
            if max_attempts == otp_session.attempts:
                msg = _('Too many failed attempts. Please request a new OTP.')
            else:
                msg = _(f'Invalid OTP. {max_attempts - otp_session.attempts} attempts remaining.')
            messages.error(request, msg)

    except OTPSession.DoesNotExist:
        messages.error(request, "Invalid OTP session. Please login again.")
        return redirect('/login')
    except Exception as e:
        messages.error(request, "An error occurred. Please try again.")
        log.error(f"OTP verification error: {str(e)}")
    
    return _render_otp_page(request)


def _get_otp_session(request):
    """Get OTP session from request session."""
    otp_session_id = request.session.get('2fa_session_id')
    otp_user_id = request.session.get('2fa_user_id')
    if not otp_session_id or not otp_user_id:
        return None
    
    return OTPSession.objects.get(
        id=otp_session_id,
        user__id=otp_user_id,
        is_verified=False
    )


def _validate_otp_session(otp_session):
    """
    Validate OTP session for expiry and attempt limits.
    Returns error message if validation fails, None if valid.
    """
    if otp_session.is_expired():
        return _('OTP has expired. Please request a new one.')
    
    max_attempts = getattr(settings, 'TWO_FA_ATTEMPTS_MAX_LIMIT', 3)
    if otp_session.attempts > max_attempts:
        return _('Too many failed attempts. Please request a new OTP.')
    
    return None


def _mark_otp_verified(request, otp_session):
    """Mark OTP session as verified and update request session."""
    otp_session.is_verified = True
    otp_session.save()
    
    request.session['2fa_verified'] = True
    request.session['2fa_required'] = False
    request.session.save()


def _render_otp_page(request):
    """Render OTP verification page with context."""

    user_email = request.session.get('2fa_user_email', '')

    context = {
        'user_email': user_email,
        'resend_url': reverse('edly_app_urls:resend_otp'),
        'logout_url': reverse('edly_app_urls:cancel_two_factor_auth'),
        'platform_name': configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
    }
    return render(request=request, template_name='verify_otp.html', context=context)


def _flush_2fa_session_variables(request):
    """
    Flush all 2FA-related session variables.
    """
    session_keys_to_remove = [
        '2fa_user_backend',
        '2fa_user_id', 
        '2fa_user_email',
        '2fa_required',
        '2fa_session_id',
        '2fa_initiated',
        '2fa_redirect_uri'
    ]
    
    for key in session_keys_to_remove:
        request.session.pop(key, None)
    
    request.session.save()
