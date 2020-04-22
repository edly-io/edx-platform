"""
Django views for colaraz features application
"""
import json
from requests.models import PreparedRequest

from django.conf import settings
from django.urls import reverse
from django.views.generic.base import RedirectView

import third_party_auth
from openedx.features.colaraz_features.helpers import get_site_base_url

class AuthProviderLogoutRedirectView(RedirectView):

    def get_redirect_url(self, *args, **kwargs):
        """
        Redirects user to relative social auth provider for logout process.
        In-case no auth provider is found or the logout_url is missing in provider's configurations
        the user is redirected to edX's default logout page '/logout'
        """
        backend_name = getattr(settings, 'COLARAZ_AUTH_PROVIDER_BACKEND_NAME', None)
        if third_party_auth.is_enabled() and backend_name and self.request.session.has_key('id_token'):
            provider = [enabled for enabled in third_party_auth.provider.Registry.enabled()
                        if enabled.backend_name == backend_name]
            if provider:
                logout_url = json.loads(getattr(provider[0], 'other_settings', '{}')).get('logout_url')
                if logout_url:
                    redirect_to = self.request.META.get('HTTP_REFERER') or get_site_base_url(self.request)
                    params = {
                        'id_token_hint': self.request.session['id_token'],
                        'post_logout_redirect_uri': redirect_to
                    }
                    req = PreparedRequest()
                    req.prepare_url(logout_url, params)

                    return req.url

        return reverse('logout')
