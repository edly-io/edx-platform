import logging

from django.conf import settings
from social_core.backends.oauth import BaseOAuth2


log = logging.getLogger(__name__)

class PayPeopleOAuth2(BaseOAuth2):
    name = 'paypeople-oauth2'

    AUTHORIZATION_URL = getattr(settings, 'PAYPEOPLE_AUTHORIZATION_URL', None)

    ACCESS_TOKEN_URL = getattr(settings, 'PAYPEOPLE_ACCESS_TOKEN_URL', None)
    USER_INFO_URL = getattr(settings, 'PAYPEOPLE_USER_INFO_URL', None)

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

    def auth_complete(self, *args, **kwargs):
        """Intercept and log the full response during token exchange"""
        try:
            response = self.request_access_token(
                self.ACCESS_TOKEN_URL,
                data=self.auth_complete_params(self.validate_state()),
                headers=self.auth_headers(),
                method=self.ACCESS_TOKEN_METHOD,
            )
            log.info(f"PayPeople full token response: {response}")
        except Exception as e:
            log.error(f"PayPeople token exchange failed: {e}")

        return super().auth_complete(*args, **kwargs)

    def get_user_details(self, response):
        log.info("Paypeople user data response: %s", response)
        return {
            'username':   str(response.get('EmployeeID', '')),
            'email':      response.get('Email', ''),
            'fullname':   response.get('UserID', ''),
            'first_name': response.get('UserID', ''),
        }

    def user_data(self, access_token, *args, **kwargs):
        url = self.USER_INFO_URL
        # The access token returned from the service's token route.
        header = {"Authorization": "Bearer %s" % access_token}
        data = self.get_json(url, headers=header)
        log.info("Paypeople user data response: %s", data)
        return data

    def get_user_id(self, details, response):
        return response.get('EmployeeID') or response.get('UserID')
