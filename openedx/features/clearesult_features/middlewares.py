import logging

from django.conf import settings
from django.http import HttpResponseRedirect, Http404
from django.utils.deprecation import MiddlewareMixin
from six.moves.urllib.parse import urlencode

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.clearesult_features.auth_backend import ClearesultAzureADOAuth2
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
        allowed_sub_paths = getattr(settings, 'CLEARESULT_ALLOWED_SUB_PATHS', [])
        allowed_full_paths = getattr(settings, 'CLEARESULT_ALLOWED_FULL_PATHS', [])

        is_allowed = any([request.path.startswith(path) for path in allowed_sub_paths])
        is_allowed = is_allowed or any([request.path == path for path in allowed_full_paths])
        user = request.user

        if not settings.FEATURES.get('ENABLE_AZURE_AD_LOGIN_REDIRECTION', False) or is_allowed or user.is_authenticated:
            LOGGER.info('Leaving without redirection for {}'.format(request.path))
            return

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
