import logging

from django.conf import settings
from social_core.backends.oauth import BaseOAuth2


log = logging.getLogger(__name__)

class PayPeopleOAuth2(BaseOAuth2):
    name = 'paypeople-oauth2'

    AUTHORIZATION_URL = getattr(settings, 'PAYPEOPLE_AUTHORIZATION_URL', None)

    ACCESS_TOKEN_URL = getattr(settings, 'PAYPEOPLE_ACCESS_TOKEN_URL', None)

    ACCESS_TOKEN_METHOD = 'POST'
    DEFAULT_SCOPE = ['openid']

    def auth_params(self, state=None, *args, **kwargs):
        """Add custom required parameters to authorization request"""
        params = super().auth_params(state=state, *args, **kwargs)
        params['scope'] = 'openid'
        params['origin'] = 'paypeople.app'
        if state:
            params['state'] = state
        return params

    def request_access_token(self, *args, **kwargs):
        return self.get_json(
            self.ACCESS_TOKEN_URL,
            method='POST',
            data={'Token': kwargs.get('code')}
        )

    def auth_complete_params(self, state=None):
        return {
            'Token': self.data.get('code'),
        }

    def get_user_details(self, response):
        log.info("Paypeople user data response: %s", response)
        return {
            'username':   str(response.get('EmployeeID', '')),
            'email':      response.get('Email', ''),
            'fullname':   response.get('UserID', ''),
            'first_name': response.get('UserID', ''),
        }

    def user_data(self, access_token, *args, **kwargs):
        return access_token

    def get_user_id(self, details, response):
        return response.get('EmployeeID') or response.get('UserID')
