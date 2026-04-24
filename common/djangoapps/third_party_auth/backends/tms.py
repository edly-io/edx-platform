# pylint: disable=W0223
"""
Custom python-social-auth OAuth2 backend for TMS (Training Management System) integration.

This backend enables seamless SSO from a client's TMS into OpenEdX:
  - User clicks "View Course" / "Continue Course" on TMS
  - Browser is redirected to /auth/login/tms-oauth2/?next=<course_url>
  - OAuth2 handshake with TMS (transparent — user already logged into TMS)
  - User is auto-created in OpenEdX using email + user_id from TMS claims
  - User is auto-enrolled in the course extracted from the next URL
  - User is automatically logged in and redirected to the course home page

Docs:
  python-social-auth backend implementation:
  https://python-social-auth.readthedocs.io/en/latest/backends/implementation.html#oauth
"""

import logging
import re
from urllib.parse import unquote

from django.utils.functional import cached_property
from social_core.backends.oauth import BaseOAuth2

log = logging.getLogger(__name__)


class TMSOAuth2(BaseOAuth2):
    """
    OAuth2 backend for TMS (Training Management System).

    TMS acts as the Identity Provider (IdP).
    OpenEdX acts as the Service Provider (SP).

    The TMS must expose the following OAuth2 / OIDC endpoints,
    configured via the OpenEdX Django admin Provider Configuration (OAuth):
      - auth_url      : OAuth2 authorization endpoint
      - token_url     : OAuth2 token endpoint
      - user_info_url : OIDC userinfo endpoint (returns email, sub, name, etc.)

    Expected userinfo response claims:
      - sub           : Unique TMS user identifier (used as the social auth UID)
      - email         : User's email address
      - name          : Full name (optional)
      - given_name    : First name (optional)
      - family_name   : Last name (optional)

    Configurable claim key overrides (set in Provider Configuration > Other settings JSON):
      - "email_claim_key"      : defaults to "email"
      - "username_claim_key"   : defaults to "email" prefix before @
      - "full_name_claim_key"  : defaults to "name"
      - "first_name_claim_key" : defaults to "given_name"
      - "last_name_claim_key"  : defaults to "family_name"
      - "user_id_claim_key"    : defaults to "sub"
    """

    name = 'tms-oauth2'
    REDIRECT_STATE = False
    ACCESS_TOKEN_METHOD = 'POST'
    DEFAULT_SCOPE = ['openid', 'email', 'profile']
    ID_KEY = 'sub'

    # ------------------------------------------------------------------ #
    # Endpoint resolution — pulled from DB provider config at runtime     #
    # ------------------------------------------------------------------ #

    def authorization_url(self):
        """Return TMS OAuth2 authorization endpoint from provider config."""
        return self._config.get_setting('auth_url')

    def access_token_url(self):
        """Return TMS OAuth2 token endpoint from provider config."""
        return self._config.get_setting('token_url')

    # ------------------------------------------------------------------ #
    # User data                                                           #
    # ------------------------------------------------------------------ #

    def user_data(self, access_token, *args, **kwargs):
        """
        Fetch user profile from TMS userinfo endpoint using the access token.
        Returns a dict of OIDC claims (email, sub, name, etc.).
        """
        url = self._config.get_setting('user_info_url')
        return self.get_json(url, headers={'Authorization': f'Bearer {access_token}'})

    def get_user_details(self, response):
        """
        Map TMS OIDC claims to OpenEdX user fields.

        OpenEdX pipeline uses these details to populate the new user record
        when auto-creating an account (skip_registration_form = True).
        """
        email = response.get(self._claim('email_claim_key', 'email'), '')

        # Derive a username from the email prefix if no dedicated claim exists.
        username_from_email = email.split('@')[0] if email else ''

        return {
            'email':      email,
            'username':   response.get(self._claim('username_claim_key', 'user_id'), username_from_email),
            'fullname':   response.get(self._claim('full_name_claim_key', 'name'), ''),
            'first_name': response.get(self._claim('first_name_claim_key', 'given_name'), ''),
            'last_name':  response.get(self._claim('last_name_claim_key', 'family_name'), ''),
        }

    def get_user_id(self, details, response):
        """
        Return the unique TMS user identifier.

        This value is stored as the social auth UID and is used on every
        subsequent login to look up the existing OpenEdX user — preventing
        duplicate account creation across sessions.

        Defaults to the OIDC 'sub' claim but is configurable via
        'user_id_claim_key' in the provider's Other settings JSON.
        """
        claim_key = self._claim('user_id_claim_key', self.ID_KEY)
        try:
            return response.get(claim_key)
        except (KeyError, AttributeError):
            return None

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _claim(self, setting_name, default):
        """
        Return the configured claim key name, falling back to *default*.
        Allows operators to remap TMS claim names via the admin UI without
        touching code.
        """
        try:
            return self._config.get_setting(setting_name)
        except KeyError:
            return default

    @cached_property
    def _config(self):
        """Lazy-load the OAuth2ProviderConfig for this backend from the DB."""
        from common.djangoapps.third_party_auth.models import OAuth2ProviderConfig  # pylint: disable=import-outside-toplevel
        return OAuth2ProviderConfig.current(self.name)


# ── Pipeline step ─────────────────────────────────────────────────────────── #

# Matches course keys of the form: course-v1:Org+Course+Run
_COURSE_KEY_RE = re.compile(r'(course-v1:[A-Za-z0-9_.+-]+\+[A-Za-z0-9_.+-]+\+[A-Za-z0-9_.+-]+)')


def enroll_user_in_course(backend, user, *args, **kwargs):
    """
    Social-auth pipeline step: auto-enroll the authenticated user in the
    course referenced by the ``next`` redirect URL.

    Only activates for the ``tms-oauth2`` backend so it does not interfere
    with any other third-party auth providers.

    Register this step in SOCIAL_AUTH_PIPELINE (lms/envs/common.py) immediately
    after ``social_core.pipeline.user.create_user``:

        'common.djangoapps.third_party_auth.backends.tms.enroll_user_in_course',

    Pipeline contract: returning None continues the pipeline unchanged.
    """
    if backend.name != TMSOAuth2.name:
        return

    if user is None:
        return

    # social-auth stores the original ?next= value in the session under 'next'.
    strategy = kwargs.get('strategy')
    next_url = ''
    if strategy is not None:
        next_url = strategy.session_get('next') or ''
    if not next_url:
        # Fall back to the live request param (first leg of the flow).
        request = getattr(strategy, 'request', None)
        if request is not None:
            next_url = request.GET.get('next', '')

    next_url = unquote(next_url)

    match = _COURSE_KEY_RE.search(next_url)
    if not match:
        log.debug('TMS OAuth2 enrollment: no course key found in next URL %r — skipping', next_url)
        return

    course_id = match.group(1)

    try:
        from opaque_keys.edx.keys import CourseKey  # pylint: disable=import-outside-toplevel
        from common.djangoapps.student.models import CourseEnrollment  # pylint: disable=import-outside-toplevel

        course_key = CourseKey.from_string(course_id)

        if CourseEnrollment.is_enrolled(user, course_key):
            log.info('TMS OAuth2 enrollment: user %s already enrolled in %s', user.username, course_id)
            return

        CourseEnrollment.enroll(user, course_key, check_access=True)
        log.info('TMS OAuth2 enrollment: enrolled user %s in %s', user.username, course_id)

    except Exception:  # pylint: disable=broad-except
        # Never break the login flow because of an enrollment failure.
        log.exception('TMS OAuth2 enrollment: failed to enroll user %s in %s', user.username, course_id)
