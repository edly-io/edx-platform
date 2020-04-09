import logging

from django.utils.functional import cached_property
from social_core.exceptions import AuthException

from openedx.core.djangoapps.theming.helpers import get_current_request
from third_party_auth.identityserver3 import IdentityServer3

LOGGER = logging.getLogger(__name__)


class ColarazIdentityServer(IdentityServer3):
    """
    An extension of the IdentityServer3 for use with Colaraz's IdP service.
    """
    name = "colarazIdentityServer"
    DEFAULT_SCOPE = ["openid", "profile", "IdentityServerApi"]
    ID_KEY = "email"

    def get_redirect_uri(self, state=None):
        """
        Returns redirect uri for oauth redirection
        """
        current_req = get_current_request()

        environ = getattr(current_req, "environ", {})
        schema = environ.get("wsgi.url_scheme", "http")

        site = getattr(current_req, "site", None)
        domain = getattr(site, "domain", None)

        if not domain:
            LOGGER.exception("Domain not found in request attributes")
            raise AuthException("Colaraz", "Error while authentication")

        return "{}://{}/auth/complete/{}".format(schema, domain, self.name)

    def get_user_details(self, response):
        """
        Returns detail about the user account from the service
        """

        current_req = get_current_request()
        site = getattr(current_req, "site", None)
        domain = getattr(site, "domain", None)

        user_site_domain = response["companyInfo"]["url"]

        if not domain:
            LOGGER.exception("Domain not found in request attributes")
            raise AuthException("Colaraz", "Error while authentication")
        elif user_site_domain.lower() not in domain:
            LOGGER.exception("User can only login through {} site".format(user_site_domain))
            raise AuthException("Colaraz", "Your account belongs to {}".format(user_site_domain))

        details = {
            "fullname": "{} {}".format(response["firstName"], response["lastName"]),
            "email": response["email"],
            "first_name": response["firstName"],
            "last_name": response["lastName"],
            "username": response["email"]
        }
        return details

    @cached_property
    def _id3_config(self):
        from third_party_auth.models import OAuth2ProviderConfig
        return OAuth2ProviderConfig.current(self.name)
