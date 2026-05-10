"""
Tests for the AuthoringGrading API (ADR 0028 ViewSet migration).

Two test classes:
  - TestAuthoringGradingViewSetPermissions — auth/permission boundaries (APITestCase, no MongoDB)
  - TestAuthoringGradingViewSetUpdate      — successful PATCH + deprecated alias (APITestCase + mocks, no MongoDB)
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.tests.factories import UserFactory

COURSE_ID = 'course-v1:edX+ToyX+Toy_Course'

# Minimal graders payload accepted by CourseGradingModelSerializer
_GRADERS_PAYLOAD = [
    {
        'type': 'Homework',
        'min_count': 1,
        'drop_count': 0,
        'short_label': '',
        'weight': 100,
        'id': 0,
    }
]

# Fake CourseGradingModel return value: only the field the serializer reads
_MOCK_GRADING_MODEL = SimpleNamespace(graders=_GRADERS_PAYLOAD)


class TestAuthoringGradingViewSetPermissions(APITestCase):
    """
    ADR 0028 – permission boundary tests for AuthoringGradingViewSet.partial_update.

    These tests only exercise auth/permission enforcement and do not require
    a real course in the module store (MongoDB not needed).
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse(
            'cms.djangoapps.contentstore:v0:authoring-grading-detail',
            kwargs={'course_id': COURSE_ID},
        )

    def test_unauthenticated_patch_gets_401(self):
        """Unauthenticated PATCH must be rejected with 401 before reaching business logic."""
        response = self.client.patch(self.url, {}, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_staff_patch_gets_403(self):
        """Authenticated non-staff user must be rejected with 403 (HasStudioReadAccess fails)."""
        non_staff = UserFactory.create()
        self.client.force_authenticate(user=non_staff)
        response = self.client.patch(self.url, {}, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestAuthoringGradingViewSetUpdate(APITestCase):
    """
    ADR 0028 – functional tests for AuthoringGradingViewSet.partial_update.

    Uses APITestCase with targeted mocks so no MongoDB connection is required:
      - CourseOverview.course_exists          -> True               (bypasses module-store lookup)
      - CourseGradingModel.update_from_json   -> _MOCK_GRADING_MODEL (no module-store write)
      - update_credit_course_requirements.delay -> no-op
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        # GlobalStaff (is_staff=True) passes HasStudioReadAccess without a real course
        self.staff_user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=self.staff_user)

        # Router-generated detail URL: PATCH /api/contentstore/v0/grading/{course_id}/
        self.url = reverse(
            'cms.djangoapps.contentstore:v0:authoring-grading-detail',
            kwargs={'course_id': COURSE_ID},
        )
        # Deprecated backward-compat alias: POST /api/contentstore/v0/grading/{course_id}
        self.deprecated_url = reverse(
            'cms.djangoapps.contentstore:v0:cms_api_update_grading',
            kwargs={'course_id': COURSE_ID},
        )

    @patch(
        'openedx.core.djangoapps.credit.tasks.update_credit_course_requirements.delay'
    )
    @patch(
        'cms.djangoapps.contentstore.rest_api.v0.views.authoring_grading.CourseGradingModel.update_from_json',
        return_value=_MOCK_GRADING_MODEL,
    )
    @patch(
        'cms.djangoapps.contentstore.rest_api.v0.views.authoring_grading.CourseOverview.course_exists',
        return_value=True,
    )
    def test_staff_patch_updates_grading_returns_200(
        self, mock_exists, mock_update, mock_credit_task
    ):
        """
        Staff PATCH with valid graders data returns HTTP 200 and fires the
        update_credit_course_requirements Celery task when minimum_grade_credit is present.
        """
        request_data = {
            'graders': _GRADERS_PAYLOAD,
            'grade_cutoffs': {'A': 0.75, 'B': 0.63, 'C': 0.57, 'D': 0.5},
            'grace_period': {'hours': 12, 'minutes': 0},
            'minimum_grade_credit': 0.7,
            'is_credit_course': True,
        }
        response = self.client.patch(
            self.url,
            data=json.dumps(request_data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_update.assert_called_once()
        mock_credit_task.assert_called_once()

    @patch(
        'openedx.core.djangoapps.credit.tasks.update_credit_course_requirements.delay'
    )
    @patch(
        'cms.djangoapps.contentstore.rest_api.v0.views.authoring_grading.CourseGradingModel.update_from_json',
        return_value=_MOCK_GRADING_MODEL,
    )
    @patch(
        'cms.djangoapps.contentstore.rest_api.v0.views.authoring_grading.CourseOverview.course_exists',
        return_value=True,
    )
    def test_deprecated_post_alias_still_returns_200(
        self, mock_exists, mock_update, mock_credit_task
    ):
        """
        Deprecated POST /grading/{course_id} alias must still return 200 during the
        deprecation window (backward compatibility requirement of ADR 0028).
        """
        request_data = {
            'graders': _GRADERS_PAYLOAD,
            'grade_cutoffs': {'A': 0.75, 'B': 0.63, 'C': 0.57, 'D': 0.5},
            'grace_period': {'hours': 12, 'minutes': 0},
        }
        response = self.client.post(
            self.deprecated_url,
            data=json.dumps(request_data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# ADR 0029 – Standardize Error Responses
# ---------------------------------------------------------------------------

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")

class TestAuthoringGradingViewSetErrorShape(APITestCase):
    """
    ADR 0029 – error response shape regression tests for AuthoringGradingViewSet.

    Verifies that 404 error responses conform to the standardized JSON envelope
    after removing DeveloperErrorViewMixin and replacing @verify_course_exists()
    with inline NotFound raises.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.staff_user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=self.staff_user)
        self.url = reverse(
            'cms.djangoapps.contentstore:v0:authoring-grading-detail',
            kwargs={'course_id': COURSE_ID},
        )

    @patch(
        'cms.djangoapps.contentstore.rest_api.v0.views.authoring_grading.CourseOverview.course_exists',
        return_value=False,
    )
    def test_nonexistent_course_returns_not_found_envelope(self, mock_exists):
        """PATCH for a course that does not exist must return 404 with ADR 0029 envelope."""
        response = self.client.patch(self.url, {}, content_type='application/json')
        self.assertEqual(response.status_code, 404)
        for field in _REQUIRED_ERROR_FIELDS:
            self.assertIn(field, response.data, f"ADR 0029: missing error field '{field}'")

    @patch(
        'cms.djangoapps.contentstore.rest_api.v0.views.authoring_grading.CourseOverview.course_exists',
        return_value=False,
    )
    def test_not_found_error_type_uri(self, mock_exists):
        """The ``type`` field must be the ADR 0029 not-found URI."""
        response = self.client.patch(self.url, {}, content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data.get('type'), 'https://docs.openedx.org/errors/not-found')

    @patch(
        'cms.djangoapps.contentstore.rest_api.v0.views.authoring_grading.CourseOverview.course_exists',
        return_value=False,
    )
    def test_instance_field_is_request_path(self, mock_exists):
        """The ``instance`` field must equal the request path."""
        response = self.client.patch(self.url, {}, content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data.get('instance'), self.url)

    @patch(
        'cms.djangoapps.contentstore.rest_api.v0.views.authoring_grading.CourseOverview.course_exists',
        return_value=False,
    )
    def test_no_developer_message_in_error_body(self, mock_exists):
        """Response must NOT contain the old DeveloperErrorViewMixin 'developer_message' key."""
        response = self.client.patch(self.url, {}, content_type='application/json')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('developer_message', response.data)
        self.assertNotIn('error_code', response.data)
