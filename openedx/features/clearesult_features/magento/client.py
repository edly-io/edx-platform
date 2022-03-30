"""
Client to handle Magento requests.
"""
import json
import logging
import requests
from django.conf import settings
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.clearesult_features.magento.exceptions import MissingMagentoUserKey, InvalidMagentoResponseError

logger = logging.getLogger(__name__)


class MagentoClient(object):
    """
    Client for Paystack API requests.
    """
    _POST_METHOD = 'POST'
    _GET_METHOD = 'GET'
    _PUT_METHOD = 'PUT'
    _CONTENT_TYPE = 'application/json'

    def __init__(self, user, base_api_url=None, base_token=None, base_redirect=None):
        """
        Constructs a new instance of the Magento client.
        """

        if not base_api_url:
            self._BASE_API_END_POINT = configuration_helpers.get_value(
                'MAGENTO_BASE_API_URL', settings.MAGENTO_BASE_API_URL)
        else:
            self._BASE_API_END_POINT = base_api_url

        if not base_redirect:
            self._REDIRECT_URL = configuration_helpers.get_value(
                'MAGENTO_REDIRECT_URL', settings.MAGENTO_REDIRECT_URL)
        else:
            self._REDIRECT_URL = base_redirect

        if not base_token:
            self._MAGENTO_LMS_INTEGRATION_TOKEN = configuration_helpers.get_value(
                'MAGENTO_LMS_INTEGRATION_TOKEN', settings.MAGENTO_LMS_INTEGRATION_TOKEN)
        else:
            self._MAGENTO_LMS_INTEGRATION_TOKEN = base_token

        self._MAGENTO_USER_KEY = None

        self.generate_costomer_token(user)
        if not self._MAGENTO_USER_KEY:
            raise MissingMagentoUserKey("Magento Client Error: Unable to get User Key from Magento.")

    def get_url(self, path):
        """
        Creates a request URL by appending path with base end point.
        """
        url = self._BASE_API_END_POINT
        if path:
            url += path
        return url

    def get_headers(self, token=None):
        """
        Returns Request Header required to send Paystack API.
        """
        headers = {'Content-Type': self._CONTENT_TYPE}
        if token:
            headers.update({'Authorization': 'Bearer ' + token})
        return headers

    def parse_response(self, response):
        """
        Parses and return the response.
        """
        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code in [200, 201]:
            logger.info("Magento API returned success response: %s.", json.dumps(data))
            return True, data

        else:
            if data and data.get('message') == 'The requested qty exceeds the maximum qty allowed in shopping cart':
                logger.info("Max quantity allowed check failed at Magento side.")
                return True, data

            logger.error("Magento API return response with status code: %s.", response.status_code)
            logger.error("Magento API return Error response: %s.", json.dumps(data))
            return False, data

    def handle_request(self, path, method, headers=None, data=None):
        """
        Handles all Magento API calls.
        """
        if not headers:
            headers = self.get_headers()

        method_map = {
            self._GET_METHOD: requests.get,
            self._POST_METHOD: requests.post,
            self._PUT_METHOD: requests.put
        }

        request = method_map.get(method)
        payload = json.dumps(data) if data else data
        url = self.get_url(path)

        if not request:
            logger.error("Request method not recognised or implemented.")

        logger.info("Sending Magento %s request on URL: %s.", method, url)
        response = request(url=url, headers=headers, data=payload)
        return self.parse_response(response)

    def get_customer_cart(self):
        success, cart_id = self.handle_request(
            'carts/mine', self._POST_METHOD, self.get_headers(self._MAGENTO_USER_KEY)
        )
        if success:
            return cart_id

    def get_customer_data(self):
        success, user_info = self.handle_request(
            'customers/me', self._GET_METHOD, self.get_headers(self._MAGENTO_USER_KEY)
        )
        if success:
            return user_info


    def add_product_to_cart(self, product_sku, quantity=1):
        cart_id = self.get_customer_cart()
        if cart_id:
            data = {
                'cartItem': {
                    'sku': product_sku,
                    'qty': quantity,
                    'quote_id': cart_id
                }
            }
            success, data = self.handle_request(
                'carts/mine/items', self._POST_METHOD, self.get_headers(self._MAGENTO_USER_KEY), data
            )
            if success:
                return
        raise InvalidMagentoResponseError("Unable to add product in Magento user cart.")

    def generate_costomer_token(self, user):
        data = {
            'email': user.email,
            'firstName': user.first_name,
            'lastName': user.last_name,
            'username': user.username
        }
        success, data = self.handle_request(
            'costomer/token/getUserToken',
            self._POST_METHOD,
            self.get_headers(self._MAGENTO_LMS_INTEGRATION_TOKEN),
            data
        )
        if success:
            self._MAGENTO_USER_KEY = data

    def update_customer_with_address(self, customer_data):
        success, data = self.handle_request(
            'customers/me',
            self._PUT_METHOD,
            self.get_headers(self._MAGENTO_USER_KEY),
            {'customer': customer_data}
        )
        return success

    def get_region_details(self, country_code, region_code):
        success, data = self.handle_request(
            'region/exist/{}/{}'.format(country_code, region_code),
            self._GET_METHOD,
            self.get_headers(self._MAGENTO_LMS_INTEGRATION_TOKEN)
        )

        if not data:
            logger.info(
                "Magento API - Unable to get region data with country: {}, region: {}".format(country_code, region_code))

        if success and len(data):
            return data
