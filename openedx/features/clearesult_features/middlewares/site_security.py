"""
Site
"""
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse_lazy, reverse
from django.utils.http import urlquote_plus

from openedx.features.clearesult_features.authentication.utils import is_user_authenticated_for_site
from openedx.features.clearesult_features.models import ClearesultSiteConfiguration

ALLOWED_PATHS = getattr(settings, 'CLEARESULT_SITE_SECURITY_ALLOWED_PATHS', [])
ALLOWED_SUBPATHS = getattr(settings, 'CLEARESULT_ALLOWED_SUB_PATHS', [])


class SiteAuthenticationMiddleware:
    """
    Enforces the user to provide site security code before they can
    visit any page in site.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _is_allowed_path(self, url):
        return url in ALLOWED_PATHS or any([url.startswith(subpath) for subpath in ALLOWED_SUBPATHS])

    def __call__(self, request):
        clearesult_site_config = ClearesultSiteConfiguration.current(request.site)

        if (
            request.user.is_authenticated and
            not is_user_authenticated_for_site(request) and
            request.path not in ALLOWED_PATHS and
            not self._is_allowed_path(request.path) and
            clearesult_site_config and
            clearesult_site_config.enabled and
            clearesult_site_config.security_code_required
        ):
            url = '{url}?next={next}'.format(
                url=reverse('clearesult_features:site_security_code'),
                next=urlquote_plus(request.path)
            )
            return redirect(url)
        response = self.get_response(request)

        return response
