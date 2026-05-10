"""
ADR 0029 – Standardized error-response tests for home views (v2).

Both HomeCoursesViewSetV2 and HomePageCoursesViewV2 are free of
DeveloperErrorViewMixin and inline error returns. These tests are regression
tests verifying that the central exception handler produces the correct
ADR 0029 envelope for auth errors.
"""
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")


class TestHomeCoursesViewSetV2ErrorShape(APITestCase):
    """
    ADR 0029 – error response shape regression tests for HomeCoursesViewSetV2.

    Verifies that 401 responses on the list action conform to the standardized
    JSON envelope.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.list_url = reverse("cms.djangoapps.contentstore:v2:home-courses-list")

    def test_unauthenticated_returns_standardized_401(self):
        """Unauthenticated GET must return 401 with the ADR 0029 envelope."""
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        for field in _REQUIRED_ERROR_FIELDS:
            self.assertIn(field, response.data, f"ADR 0029: missing field '{field}'")

    def test_unauthenticated_401_type_uri(self):
        """The ``type`` field for 401 must be the ADR 0029 authn URI."""
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data.get("type"), "https://docs.openedx.org/errors/authn")

    def test_error_body_has_no_developer_message(self):
        """Error responses must NOT contain old DeveloperErrorViewMixin fields."""
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("developer_message", response.data)
        self.assertNotIn("error_code", response.data)

    def test_instance_field_is_request_path(self):
        """The ``instance`` field must equal the request path."""
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data.get("instance"), self.list_url)
