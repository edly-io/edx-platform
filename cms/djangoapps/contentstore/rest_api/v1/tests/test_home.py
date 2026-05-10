"""
ADR 0029 – Standardized error-response tests for HomeViewSet.

All view methods in home.py are free of inline error returns and
DeveloperErrorViewMixin, so this file contains regression tests verifying
that the central exception handler produces the correct ADR 0029 envelope
for auth errors on each action.
"""
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")


class TestHomeViewSetErrorShape(APITestCase):
    """
    ADR 0029 – error response shape regression tests for HomeViewSet.

    Verifies that 401 responses on all three actions conform to the
    standardized JSON envelope.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.list_url = reverse("cms.djangoapps.contentstore:v1:home-list")
        self.courses_url = reverse("cms.djangoapps.contentstore:v1:home-courses")
        self.libraries_url = reverse("cms.djangoapps.contentstore:v1:home-libraries")

    def test_unauthenticated_list_returns_standardized_401(self):
        """Unauthenticated GET /home/ must return 401 with the ADR 0029 envelope."""
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    def test_unauthenticated_list_401_type_uri(self):
        """The ``type`` field for 401 must be the ADR 0029 authn URI."""
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("type") == "https://docs.openedx.org/errors/authn"

    def test_unauthenticated_courses_returns_standardized_401(self):
        """Unauthenticated GET /home/courses/ must return 401 with the ADR 0029 envelope."""
        response = self.client.get(self.courses_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    def test_unauthenticated_libraries_returns_standardized_401(self):
        """Unauthenticated GET /home/libraries/ must return 401 with the ADR 0029 envelope."""
        response = self.client.get(self.libraries_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        for field in _REQUIRED_ERROR_FIELDS:
            assert field in response.data, f"ADR 0029: missing field '{field}'"

    def test_error_body_has_no_developer_message(self):
        """Error responses must NOT contain old DeveloperErrorViewMixin fields."""
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "developer_message" not in response.data
        assert "error_code" not in response.data

    def test_instance_field_is_request_path(self):
        """The ``instance`` field must equal the request path."""
        response = self.client.get(self.list_url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("instance") == self.list_url
