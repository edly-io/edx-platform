import logging
import json
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
    REDIRECT_STATE = False
    STATE_PARAMETER = True

    def auth_params(self, state=None, *args, **kwargs):
        """Add custom required parameters to authorization request"""
        params = super().auth_params(state=state, *args, **kwargs)
        params['scope'] = 'openid'
        params['origin'] = 'paypeople.app'
        if state:
            params['state'] = state
        return params

    def auth_complete_params(self, state=None):
        """
        PayPeople expects ONLY Token in the body.
        Docs: POST /RefreshToken with {"Token": "<auth_code>"}
        """
        code = self.data.get('code')
        log.info("=== PayPeople Auth Code received: %s ===", code)
        return {
            'Token': code
        }

    def request_access_token(self, *args, **kwargs):
        """
        Override to send JSON body instead of form data
        since PayPeople expects a JSON payload.
        """
        log.info("=== PayPeople TOKEN REQUEST ===")
        log.info("URL: %s", self.ACCESS_TOKEN_URL)
        log.info("Payload: %s", kwargs.get('data'))

        # PayPeople expects JSON not form-encoded
        response = self.get_json(
            self.ACCESS_TOKEN_URL,
            method='POST',
            json=kwargs.get('data'),   # send as JSON body
            headers={'Content-Type': 'application/json'}
        )

        log.info("=== PayPeople TOKEN RESPONSE ===")
        log.info("Response: %s", response)

        return response

    def process_error(self, data):
        """Handle errors in token response"""
        if data.get('IsSuccess') is False:
            error = data.get('ErrorMessage') or data.get('Message') or 'Unknown error'
            log.error("=== PayPeople error: %s ===", error)
            raise Exception(f"PayPeople SSO error: {error}")

    def get_access_token(self, response):
        """
        PayPeople returns 'Token' instead of standard 'access_token'.
        Extract it here.
        """
        token = response.get('Token') or response.get('AuthToken')
        log.info("=== PayPeople extracted access_token: %s ===", token)
        return token

    def auth_complete(self, *args, **kwargs):
        import json

        self.process_error(self.data)

        # Fix + signs decoded as spaces
        raw_code = self.data.get('code', '')
        code = raw_code.replace(' ', '+')

        log.info("=== PayPeople Fixed code: %s ===", code)

        token_response = self.get_json(
            self.ACCESS_TOKEN_URL,
            method='POST',
            data=json.dumps({'Token': code}),
            headers={'Content-Type': 'application/json'}
        )

        log.info("=== PayPeople Token Response: %s ===", token_response)

        # Token is nested inside ResultSet
        result_set = token_response.get('ResultSet', {})
        access_token = result_set.get('Token')

        log.info("=== PayPeople Extracted access_token: %s ===", access_token)

        if not access_token:
            log.error("=== PayPeople: No access token in ResultSet: %s ===", result_set)
            raise Exception("PayPeople: No access token in response")

        # Pass result_set as response so get_user_details gets the right fields
        kwargs.update({
            'response': result_set,
            'access_token': access_token
        })

        return self.strategy.authenticate(self, *args, **kwargs)

    def get_user_details(self, response):
        log.info("=== PayPeople GET USER DETAILS: %s ===", response)
        return {
            'username': str(int(response.get('EmployeeID', 0))),  # remove .0 from float
            'email': response.get('Email', ''),
            'fullname': response.get('CompanyName', ''),
            'first_name': response.get('UserID', ''),
            'last_name': '',
        }

    def user_data(self, access_token, *args, **kwargs):
        """
        Fetch additional user info if needed.
        PayPeople may already return user info in token response.
        """
        log.info("=== PayPeople USER DATA REQUEST, token: %s ===", access_token)

        # If token response already had user data, return it from kwargs
        response = kwargs.get('response', {})
        if response.get('EmployeeID') or response.get('UserID'):
            log.info("=== PayPeople Using user data from token response ===")
            return response

        # Otherwise call userinfo endpoint
        url = self.USER_INFO_URL
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        data = self.get_json(url, headers=headers)
        log.info("=== PayPeople USER INFO RESPONSE: %s ===", data)
        return data

    def get_user_id(self, details, response):
        employee_id = response.get('EmployeeID')
        return str(int(employee_id)) if employee_id else None
