# pylint: disable=W0223
"""
python-social-auth backend for use with Okta
link: https://github.com/python-social-auth/social-core/blob/master/social_core/backends/okta_openidconnect.py
"""
from logging import getLogger

from social_core.backends.okta_openidconnect import OktaOpenIdConnect
from django.utils.functional import cached_property

logger = getLogger(__name__)


class OktaOAuth2(OktaOpenIdConnect):
    """
    An extension of the OktaOpenIdConnect for use with an OktaOAuth2 service.
    link: https://github.com/python-social-auth/social-core/blob/master/social_core/backends/okta_openidconnect.py
    """

    def get_user_details(self, response):
        """
        Return details about the user account from the service
        """
        user_details = {
            "username": response.get("preferred_username"),
            "email": response.get("email") or "",
            "first_name": response.get("given_name"),
            "last_name": response.get("family_name"),
            "role": response.get("user_role"),
            "groups": response.get("groups"),
        }
        logger.info("OktaOAuth2 response: %s", response)
        logger.info("OktaOAuth2 user_details: %s", user_details)
        return user_details
