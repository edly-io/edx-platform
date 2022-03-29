import logging

from django.conf import settings
from django.contrib.auth import logout
from django.core.cache import cache
from django.http import HttpResponseRedirect, Http404
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from six.moves.urllib.parse import urlencode

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.clearesult_features.auth_backend import ClearesultAzureADOAuth2
from openedx.features.clearesult_features.constants import (
    ALLOWED_SITES_NAMES_CACHE_KEY_SUFFIX, ALLOWED_SITES_NAMES_CACHE_TIMEOUT
)
from openedx.features.clearesult_features.models import ClearesultUserProfile
import third_party_auth
from third_party_auth import pipeline

LOGGER = logging.getLogger(__name__)


class ClearesultAuthenticationMiddleware(MiddlewareMixin):
    """
    Verify User's accessibility over his requested site
    """
    def process_request(self, request):
        """
        Django middleware hook for processing request
        """
        if self._is_user_suspicious(request):
            LOGGER.info('Suspicious user: redirecting to logout')
            logout(request)
            return HttpResponseRedirect(reverse('root'))
        user = request.user
        if not settings.FEATURES.get('ENABLE_AZURE_AD_LOGIN_REDIRECTION', False):
            LOGGER.info('Leaving without redirection for {}, Azure AD Redirection is disabled.'.format(request.path))
            return


        # Blocking all the paths which need to be shown to logged in users only
        blocked_sub_paths = getattr(settings, 'CLEARESULT_BLOCKED_SUBPATH', [])
        blocked_full_paths = getattr(settings, 'CLEARESULT_BLOCKED_FULL_PATH', [])
        allowed_paths = getattr(settings, 'CLEARESULT_ALLOWED_SUB_PATHS', [])
        allowed_included_paths = getattr(settings, 'CLEARESULT_ALLOWED_INCLUDED_PATHS', [])

        # Allow API calls
        # Allowed URLS will have high priority over blocked URLS.
        if(any([allowed_path in request.path for allowed_path in allowed_paths])):
            LOGGER.info('Leaving without redirection for allowed path {}'.format(request.path))
            return

        if any([allowed_included_path in request.path for allowed_included_path in allowed_included_paths]):
            LOGGER.info('Leaving without redirection for suffix path {}'.format(request.path))
            return

        # Blocking all the paths which need to be shown to logged in users only
        is_blocked = [blocked_path in request.path for blocked_path in blocked_sub_paths]
        is_blocked += [blocked_path == request.path for blocked_path in blocked_full_paths]

        if not user.is_authenticated and any(is_blocked):
            LOGGER.info('Need to login for: {}'.format(request.path))
            return self._redirect_to_login(request)


    def _redirect_to_login(self, request):
        backend_name = ClearesultAzureADOAuth2.name

        if third_party_auth.is_enabled() and backend_name:
            provider = [enabled for enabled in third_party_auth.provider.Registry.enabled()
                        if enabled.backend_name == backend_name]
            fallback_url = '{}/login'.format(configuration_helpers.get_value('LMS_BASE'))
            if not provider and fallback_url:
                next_url = urlencode({'next': self._get_current_url(request).replace('login', 'home')})
                redirect_url = '//{}?{}'.format(fallback_url, next_url)
                LOGGER.info('No Auth Provider found, redirecting to "{}"'.format(redirect_url))
                redirect_url = redirect_url.replace('signin_redirect_to_lms', 'home')
                return HttpResponseRedirect(redirect_url)
            elif provider:
                login_url = pipeline.get_login_url(
                    provider[0].provider_id,
                    pipeline.AUTH_ENTRY_LOGIN,
                    redirect_url=request.GET.get('next') if request.GET.get('next') else request.path,
                )
                LOGGER.info('Redirecting User to Auth Provider: {}'.format(backend_name))
                return HttpResponseRedirect(login_url)

        LOGGER.error('Unable to redirect, Auth Provider is not configured properly')
        raise Http404

    def _get_request_schema(self, request):
        """
        Returns schema of request
        """
        environ = getattr(request, 'environ', {})
        return environ.get('wsgi.url_scheme', 'http')

    def _get_current_url(self, request):
        """
        Returns current request's complete url
        """
        schema = self._get_request_schema(request)
        domain = self._get_request_site_domain(request)

        return '{}://{}{}'.format(schema, domain, request.path)

    def _get_request_site_domain(self, request):
        """
        Returns domain of site being requested by the User.
        """
        site = getattr(request, 'site', None)
        domain = getattr(site, 'domain', None)
        return domain

    def _is_user_suspicious(self, request):
        user = request.user
        if user.is_authenticated:
            cache_key = user.username + ALLOWED_SITES_NAMES_CACHE_KEY_SUFFIX
            site_name = '-'.join(request.site.name.split('-')[:-1]).rstrip()
            if user.is_superuser:
                return False
            elif site_name in cache.get(cache_key, []):
                return False

            # TODO: Find a better work around for this
            # Related Comment: https://edlyio.atlassian.net/browse/EDE-1364?focusedCommentId=26939
            if request.path.startswith('/asset'):
                return False

            try:
                clearesult_allowed_site_names = user.clearesult_profile.get_identifiers()
            except ClearesultUserProfile.DoesNotExist:
                return False

            cache.set(cache_key, clearesult_allowed_site_names, ALLOWED_SITES_NAMES_CACHE_TIMEOUT)

            if site_name in clearesult_allowed_site_names:
                return False
            else:
                return True
