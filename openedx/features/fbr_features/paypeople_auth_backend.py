import logging
import json
import requests
from django.conf import settings
from social_core.backends.oauth import BaseOAuth2

log = logging.getLogger(__name__)


class PayPeopleClient:
    """
    Reusable PayPeople API client.
    First tries with SSO token, refreshes on 401 and retries once.
    """

    def __init__(self, token, company_id, user_id):
        self.token      = token
        self.company_id = company_id
        self.user_id    = user_id
        self.base_url   = getattr(
            settings,
            'PAYPEOPLE_BASE_URL',
            'https://dev.paypeople.app/ServiceApi/api'
        )
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _auth_headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "CompanyID":     self.company_id,
            "UserID":        self.user_id,
        }

    def _request(self, method, endpoint, retried=False, **kwargs):
        """
        Central request handler.
        Step 1 — Try with current SSO token.
        Step 2 — On 401, refresh token and retry once.
        Step 3 — On second 401, raise exception.
        """
        url = f"{self.base_url}/{endpoint}"
        kwargs.setdefault("headers", {}).update(self._auth_headers())

        log.info("PayPeople REQUEST [%s] %s (retried=%s)", method, url, retried)

        response = self.session.request(method, url, **kwargs)

        log.info("PayPeople RESPONSE status: %s", response.status_code)
        log.info("PayPeople RESPONSE reason: %s", response.reason)

        if response.status_code == 401:
            if retried:
                # Already retried — give up
                raise Exception("PayPeople token rejected even after refresh.")

            log.warning("PayPeople 401 on first attempt. Refreshing token...")

            if self._refresh_token():
                log.info("Token refreshed. Retrying request...")
                return self._request(method, endpoint, retried=True, **kwargs)
            else:
                raise Exception("PayPeople token refresh failed.")

        response.raise_for_status()
        return response.json()

    def _refresh_token(self):
        """Refresh access token and update instance credentials."""
        try:
            url      = f"{self.base_url}/accounts/RefreshToken"
            response = self.session.post(
                url,
                data=json.dumps({"Token": self.token})
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("IsSuccess"):
                log.error("PayPeople refresh failed: %s", data.get("ErrorMessage"))
                return False

            result          = data.get("ResultSet", {})
            self.token      = result.get("Token")
            self.company_id = result.get("CompanyID", self.company_id)
            self.user_id    = result.get("UserID", self.user_id)

            log.info("PayPeople token refreshed. Expires: %s", result.get("ExpDate"))
            return True

        except Exception as e:
            log.error("PayPeople token refresh error: %s", e)
            return False

    def get_employees(self):
        """Fetch all employees for the authenticated company."""
        try:
            data      = self._request("GET", "employee/GetEmployees")
            employees = data.get("ResultSet", [])
            log.info("PayPeople fetched %d employees.", len(employees))
            return employees
        except Exception as e:
            log.error("PayPeople failed to fetch employees: %s", e)
            return []


class PayPeopleOAuth2(BaseOAuth2):
    name = 'paypeople-oauth2'

    AUTHORIZATION_URL = getattr(settings, 'PAYPEOPLE_AUTHORIZATION_URL', None)
    ACCESS_TOKEN_URL  = getattr(settings, 'PAYPEOPLE_ACCESS_TOKEN_URL', None)
    USER_INFO_URL     = getattr(settings, 'PAYPEOPLE_USER_INFO_URL', None)

    ACCESS_TOKEN_METHOD = 'POST'
    DEFAULT_SCOPE       = ['openid']
    REDIRECT_STATE      = False
    STATE_PARAMETER     = True

    def auth_params(self, state=None, *args, **kwargs):
        params = super().auth_params(state=state, *args, **kwargs)
        params['scope']  = 'openid'
        params['origin'] = 'paypeople.app'
        if state:
            params['state'] = state
        return params

    def auth_complete_params(self, state=None):
        code = self.data.get('code')
        log.info("=== PayPeople Auth Code received: %s ===", code)
        return {'Token': code}

    def process_error(self, data):
        if data.get('IsSuccess') is False:
            error = data.get('ErrorMessage') or data.get('Message') or 'Unknown error'
            log.error("=== PayPeople error: %s ===", error)
            raise Exception(f"PayPeople SSO error: {error}")

    def auth_complete(self, *args, **kwargs):
        self.process_error(self.data)

        # Fix: + signs in code decoded as spaces
        raw_code = self.data.get('code', '')
        code     = raw_code.replace(' ', '+')

        log.info("=== PayPeople Fixed code: %s ===", code)

        token_response = self.get_json(
            self.ACCESS_TOKEN_URL,
            method='POST',
            data=json.dumps({'Token': code}),
            headers={'Content-Type': 'application/json'}
        )

        log.info("=== PayPeople Token Response: %s ===", token_response)

        result_set   = token_response.get('ResultSet', {})
        access_token = result_set.get('Token')

        if not access_token:
            log.error("=== PayPeople: No access token in ResultSet: %s ===", result_set)
            raise Exception("PayPeople: No access token in response")

        log.info("=== PayPeople Extracted access_token: %s ===", access_token)

        # Fetch employees using the token from SSO
        self._fetch_and_log_employees(
            token      = access_token,
            company_id = result_set.get('CompanyID', ''),
            user_id    = result_set.get('UserID', '')
        )

        kwargs.update({
            'response':     result_set,
            'access_token': access_token
        })

        return self.strategy.authenticate(self, *args, **kwargs)

    def _fetch_and_log_employees(self, token, company_id, user_id):
        """
        Fetch employees from PayPeople after successful SSO login.
        Uses the token obtained during authentication.
        """
        log.info("=== PayPeople Fetching employees after SSO login ===")

        try:
            client    = PayPeopleClient(token, company_id, user_id)
            employees = client.get_employees()

            if employees:
                log.info("=== PayPeople Employees fetched: %d ===", len(employees))
                for emp in employees:
                    log.info(
                        "Employee — ID: %s | Code: %s | Name: %s %s | Email: %s",
                        emp.get('EmployeeID'),
                        emp.get('EmployeeCode'),
                        emp.get('FirstName'),
                        emp.get('LastName'),
                        emp.get('EmailAddress'),
                    )
            else:
                log.warning("=== PayPeople No employees returned ===")

        except Exception as e:
            # Don't break SSO login if employee fetch fails
            log.error("=== PayPeople Employee fetch failed: %s ===", e)

    def get_user_details(self, response):
        log.info("=== PayPeople GET USER DETAILS: %s ===", response)
        return {
            'username':   str(int(response.get('EmployeeID', 0))),
            'email':      response.get('Email', ''),
            'fullname':   response.get('CompanyName', ''),
            'first_name': response.get('UserID', ''),
            'last_name':  '',
        }

    def user_data(self, access_token, *args, **kwargs):
        log.info("=== PayPeople USER DATA REQUEST, token: %s ===", access_token)

        response = kwargs.get('response', {})
        if response.get('EmployeeID') or response.get('UserID'):
            log.info("=== PayPeople Using user data from token response ===")
            return response

        url  = self.USER_INFO_URL
        data = self.get_json(
            url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type':  'application/json'
            }
        )
        log.info("=== PayPeople USER INFO RESPONSE: %s ===", data)
        return data

    def get_user_id(self, details, response):
        employee_id = response.get('EmployeeID')
        return str(int(employee_id)) if employee_id else None
