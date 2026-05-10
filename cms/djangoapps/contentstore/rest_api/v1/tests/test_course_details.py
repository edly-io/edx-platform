"""
ADR 0029 – Standardized error-response tests for CourseDetailsViewSet.

Tests that auth/permission/not-found error responses conform to the ADR 0029
JSON envelope after removing DeveloperErrorViewMixin and @verify_course_exists().
"""
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.tests.factories import UserFactory

# A syntactically valid course key that does not exist in the DB.
TEST_COURSE_ID = "course-v1:TestOrg+TestCourse+2026"
MOCK_COURSE_EXISTS = (
    "cms.djangoapps.contentstore.rest_api.v1.views.course_details.CourseOverview.course_exists"
)

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")


class TestCourseDetailsViewSetErrorShape(APITestCase):
    """
    ADR 0029 – error response shape regression tests for CourseDetailsViewSet.

    Verifies that 401, 403, and 404 responses use the standardized envelope
    after removing DeveloperErrorViewMixin and @verify_course_exists().
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.detail_url = reverse(
            "cms.djangoapps.contentstore:v1:course_details-detail",
            kwargs={"course_id": TEST_COURSE_ID},
        )

    def test_unauthenticated_get_returns_standardized_401(self):
        """Unauthenticated GET must return 401 with the ADR 0029 envelope."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        for field in _REQUIRED_ERROR_FIELDS:
            self.assertIn(field, response.data, f"ADR 0029: missing field '{field}'")

    def test_unauthenticated_401_type_uri(self):
        """The ``type`` field for 401 must be the ADR 0029 authn URI."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data.get("type"), "https://docs.openedx.org/errors/authn")

    def test_non_author_get_returns_standardized_403(self):
        """Authenticated non-author GET must return 403 with the ADR 0029 envelope."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        for field in _REQUIRED_ERROR_FIELDS:
            self.assertIn(field, response.data, f"ADR 0029: missing field '{field}'")

    def test_non_author_403_type_uri(self):
        """The ``type`` field for 403 must be the ADR 0029 authz URI."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data.get("type"), "https://docs.openedx.org/errors/authz")

    @patch(MOCK_COURSE_EXISTS, return_value=False)
    def test_nonexistent_course_returns_standardized_404(self, _mock):
        """GET for a non-existent course must return 404 with the ADR 0029 envelope."""
        staff = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=staff)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        for field in _REQUIRED_ERROR_FIELDS:
            self.assertIn(field, response.data, f"ADR 0029: missing field '{field}'")

    @patch(MOCK_COURSE_EXISTS, return_value=False)
    def test_not_found_type_uri(self, _mock):
        """The ``type`` field for 404 must be the ADR 0029 not-found URI."""
        staff = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=staff)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data.get("type"), "https://docs.openedx.org/errors/not-found")

    def test_error_body_has_no_developer_message(self):
        """Error responses must NOT contain the old DeveloperErrorViewMixin fields."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("developer_message", response.data)
        self.assertNotIn("error_code", response.data)

    def test_instance_field_is_request_path(self):
        """The ``instance`` field must equal the request path."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data.get("instance"), self.detail_url)
