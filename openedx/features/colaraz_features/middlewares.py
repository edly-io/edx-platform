import logging

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.sites.models import Site
from django.http import HttpResponseRedirect, Http404
from six.moves.urllib.parse import urlencode

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.colaraz_features.models import ColarazUserProfile
from student.models import UserAttribute
import third_party_auth
from third_party_auth import pipeline

LOGGER = logging.getLogger(__name__)


class ColarazAuthenticationMiddleware(object):
    """
    Special middleware for Colaraz which does following tasks:
    1) Verify User's accessibility over his requested site
    2) Login's User if he is logged-in on Colaraz's website
    """

    def process_request(self, request):
        """
        Django middleware hook for processing request
        """
        if getattr(settings, 'COLARAZ_ENABLE_AUTH_MIDDLEWARE', False):
            user = request.user
            
            # Blocking all the paths which need to be shown to logged in users only
            blocked_sub_paths = getattr(settings, 'COLARAZ_BLOCKED_SUB_PATHS', [])
            blocked_full_paths = getattr(settings, 'COLARAZ_BLOCKED_FULL_PATHS', [])
            is_blocked = [blocked_path in request.path for blocked_path in blocked_sub_paths]
            is_blocked += [blocked_path == request.path for blocked_path in blocked_full_paths]

            if not user.is_authenticated and any(is_blocked):
                LOGGER.info('Requested path "{}" is blocked for anonymous users'.format(request.path))
                return self._redirect_to_login(request)
            # Superuser is allowed to visit all sites
            elif user.is_authenticated and not user.is_superuser:
                request_site_domain = self._get_request_site_domain(request)
                user_site_identifier, is_new_user = self._get_user_site_identifier(user, request.session)
                if not is_new_user and not (user_site_identifier and request_site_domain and 
                        request_site_domain.startswith(user_site_identifier)):
                    LOGGER.error('User "{}" site identifier or request site do not match'.format(user.username))
                    return self._redirect_to_user_lms_domain(request, user_site_identifier)
        return

    def _redirect_to_login(self, request):
        """
        Returns HttpResponseRedirect object, redirecting User to third-party-auth
        identity provider on the basis of 'COLARAZ_AUTH_PROVIDER_BACKEND_NAME' or 
        raising Http404 page in-case auth provider isn't configured properly.
        """
        backend_name = getattr(settings, 'COLARAZ_AUTH_PROVIDER_BACKEND_NAME', None)

        if third_party_auth.is_enabled() and backend_name:
            provider = [enabled for enabled in third_party_auth.provider.Registry.enabled()
                        if enabled.backend_name == backend_name]
            fallback_url = configuration_helpers.get_value('AUTH_PROVIDER_FALLBACK_URL') \
                or configuration_helpers.get_value('LMS_BASE')
            if not provider and fallback_url:
                next_url = urlencode({'next': self._get_current_url(request)})
                redirect_url = '//{}?{}'.format(fallback_url, next_url)
                LOGGER.info('No Auth Provider found, redirecting to "{}"'.format(redirect_url))
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

    def _redirect_to_user_lms_domain(self, request, site_identifier):
        user_site = Site.objects.filter(domain__startswith='{}.courses.'.format(site_identifier))
        if user_site:
            return HttpResponseRedirect('//{}'.format(user_site[0].domain))
        else:
            LOGGER.error('Unable to redirect, User site is not configured')
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
        
    def _get_user_site_identifier(self, user, session):
        """
        Returns domain of site associated with the User.
        """
        is_new_user = False
        if not session.get('user_site_identifier', None):
            profile, is_new_user = ColarazUserProfile.objects.get_or_create(user=user)
            if not is_new_user:
                session['user_site_identifier'] = profile.site_identifier
        return session.get('user_site_identifier', None), is_new_user

    def _get_request_site_domain(self, request):
        """
        Returns domain of site being requested by the User.
        """
        site = getattr(request, 'site', None)
        domain = getattr(site, 'domain', None)
        return domain
