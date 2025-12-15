# pylint: disable=W0223
"""
python-social-auth backend for use with Okta
link: https://github.com/python-social-auth/social-core/blob/master/social_core/backends/okta_openidconnect.py
"""
from social_core.backends.okta_openidconnect import OktaOpenIdConnect


class OktaOAuth2(OktaOpenIdConnect):
    """
    An extension of the OktaOpenIdConnect for use with an OktaOAuth2 service.
    link: https://github.com/python-social-auth/social-core/blob/master/social_core/backends/okta_openidconnect.py
    """
    DEFAULT_SCOPE = ["openid", "profile", "email", "groups"]
