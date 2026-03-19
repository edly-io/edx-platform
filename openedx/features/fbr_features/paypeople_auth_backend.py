from social_core.backends.oauth import BaseOAuth2

class PayPeopleOAuth2(BaseOAuth2):
    name = 'paypeople-oauth2'

    AUTHORIZATION_URL = 'https://sso.paypeople.com/authorize'

    ACCESS_TOKEN_URL = 'https://sso.paypeople.com/RefreshToken'

    ACCESS_TOKEN_METHOD = 'POST'

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
