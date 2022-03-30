"""
Client to handle Drupal requests.
"""
import json
import logging
import requests
from django.conf import settings
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

logger = logging.getLogger(__name__)


class InvalidDrupalCredentials(Exception):
    """
    Exception raised when an Drupal Credentials or URL is missing.
    """
    pass


class DrupalClient(object):
    """
    Client for Drupal API requests.
    """
    _GET_METHOD = 'GET'

    def __init__(self):
        """
        Constructs a new instance of the Drupal client.
        """
        api_credentials = getattr(settings, 'DRUPAL_API_CREDENTIALS', {})

        self._BASE_API_END_POINT = configuration_helpers.get_value(
            'DRUPAL_API_CREDENTIALS_URL', api_credentials.get('url'))
        self._CREDENTIALS_USERNAME = configuration_helpers.get_value(
            'DRUPAL_API_CREDENTIALS_USERNAME', api_credentials.get('username'))
        self._CREDENTIALS_PASSWORD = configuration_helpers.get_value(
            'DRUPAL_API_CREDENTIALS_PASSWORD', api_credentials.get('password'))

        if not self._CREDENTIALS_USERNAME or not self._CREDENTIALS_PASSWORD:
            raise InvalidDrupalCredentials("Drupal Client Error: Credentials are missing.")

        if not self._BASE_API_END_POINT:
            raise InvalidDrupalCredentials("Drupal Client Error: BASE API URL is missing.")

    def get_url(self, path):
        """
        Creates a request URL by appending path with base end point.
        """
        url = self._BASE_API_END_POINT
        if path:
            url += path
        return url

    def parse_response(self, response):
        """
        Parses and return the response.
        """
        try:
            data = response.json()
        except ValueError:
            data = None

        if response.ok:
            logger.info("Drupal API returned success response: %s.", json.dumps(data))
            return True, data

        else:
            logger.error("Drupal API returned response with status code: %s.", response.status_code)
            logger.error("Drupal API returned Error response: %s.", json.dumps(data))
            return False, data

    def handle_request(self, path, method, data=None):
        """
        Handles all Drupal API calls.
        """
        method_map = {
            self._GET_METHOD: requests.get,
        }

        request = method_map.get(method)
        url = self.get_url(path)

        if not request:
            logger.error("Request method not recognised or implemented.")

        logger.info("Sending Drupaal %s request on URL: %s.", method, url)
        response = request(url=url, auth=(self._CREDENTIALS_USERNAME, self._CREDENTIALS_PASSWORD))
        return self.parse_response(response)

    def logout_user(self, email):
        path = "single/signout?email={}".format(email)
        success, data = self.handle_request(path, self._GET_METHOD)
        return success

    def get_user_data(self, email):
        path = "user-info?email={}".format(email)
        success, data = self.handle_request(path, self._GET_METHOD)
        if data:
            data = data.get('result', {}).get('user_info', {})
        return data
