"""
Custom auth backend for Clearesult.
"""
import logging

from social_core.backends.azuread_b2c import AzureADB2COAuth2
from social_core.exceptions import AuthException

from openedx.core.djangoapps.theming.helpers import get_current_request

LOGGER = logging.getLogger(__name__)


class ClearesultAzureADOAuth2(AzureADB2COAuth2):
    """
    Custom OAuth2 backend for Clearesult.
    """
    name = 'clearesult-azuread-oauth2'

    @property
    def base_url(self):
        return self.setting('BASE_URL').format(policy=self.policy)

    def get_redirect_uri(self, state=None):
        """
        Returns redirect uri for oauth redirection.
        """
        current_req = get_current_request()

        environ = getattr(current_req, "environ", {})
        schema = environ.get("wsgi.url_scheme", "http")

        site = getattr(current_req, "site", None)
        domain = getattr(site, "domain", None)

        if not domain:
            LOGGER.exception("Domain not found in request attributes")
            raise AuthException("Clearesult", "Error while authentication")

        return "{}://{}/auth/complete/{}".format(schema, domain, self.name)
