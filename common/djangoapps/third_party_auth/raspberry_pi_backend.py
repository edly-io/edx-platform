import importlib
import json
import logging

import jwt
from django.contrib.sites.models import Site
from jwt import DecodeError, ExpiredSignatureError
from social_core.backends.oauth import BaseOAuth2

from openedx.core.djangoapps.theming.helpers import get_current_request

logger = logging.getLogger(__name__)


class RaspberryPiOAuth2(BaseOAuth2):
    """Raspberry Pi Foundation OAuth authentication backend"""

    name = "custom-oauth2"
    AUTHORIZATION_URL = "https://auth-v2.raspberrypi.org/oauth2/auth"
    ACCESS_TOKEN_URL = "https://auth-v2.raspberrypi.org/oauth2/token"
    ACCESS_TOKEN_METHOD = "POST"
    REDIRECT_STATE = False
    DEFAULT_SCOPE = ["openid", "email", "name", "force-consent"]
    authorize_params: {
        "brand": "edly",
    }

    def get_provider_config(self):
        """Get the OAuth2ProviderConfig from database for this backend"""
        try:
            models_module = importlib.import_module('common.djangoapps.third_party_auth.models')
            OAuth2ProviderConfig = models_module.OAuth2ProviderConfig
            
            site = Site.objects.get_current(get_current_request())
            provider_config = OAuth2ProviderConfig.current(self.name, site)
            return provider_config
        except Exception as e:
            logger.error("Error getting provider config: {}".format(str(e)))
            return None

    def get_config_value(self, key, default=None):
        """Get a configuration value from the database other_settings field"""
        provider_config = self.get_provider_config()
        if provider_config and provider_config.other_settings:
            try:
                other_settings = json.loads(provider_config.other_settings)
                return other_settings.get(key, default)
            except (json.JSONDecodeError, AttributeError) as e:
                logger.error(f"Error parsing other_settings: {e}")

        return default

    def authorization_url(self):
        """Get authorization URL from database config or use default"""
        return self.get_config_value('AUTHORIZATION_URL', self.AUTHORIZATION_URL)

    def access_token_url(self):
        """Get access token URL from database config or use default"""
        return self.get_config_value('ACCESS_TOKEN_URL', self.ACCESS_TOKEN_URL)

    def get_user_id(self, details, response):
        """Use subject (sub) claim as unique id."""
        return response.get("sub")

    def user_data(self, access_token, *args, **kwargs):
        response = kwargs.get("response")
        id_token = response.get("id_token")

        try:
            return jwt.decode(
                id_token, audience="edly", options={"verify_signature": False}
            )
        except (DecodeError, ExpiredSignatureError) as error:
            logger("Error {error} while decoding id_token".format(error=error))

    def get_user_details(self, response):
        """Return user details from RPF account"""

        return {
            "username": response.get("nickname"),
            "email": response.get("email"),
            "fullname": response.get("name"),
        }
