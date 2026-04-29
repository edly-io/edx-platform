"""
Unit tests for CourseDetailsViewSet (ADR 0028).

MongoDB-free: all service-layer calls are mocked so these tests run without a
live modulestore.  Permission-boundary tests use force_authenticate with a
plain (non-staff) or global-staff User factory instance.
"""
from unittest.mock import MagicMock, patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cms.djangoapps.contentstore.rest_api.v1.views.course_details import CourseDetailsViewSet
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

# ------------------------------------------------------------------
# Mock target paths
# ------------------------------------------------------------------
MOCK_FETCH = 'openedx.core.djangoapps.models.course_details.CourseDetails.fetch'
MOCK_MODULESTORE = (
    'cms.djangoapps.contentstore.rest_api.v1.views.course_details.modulestore'
)
MOCK_UPDATE = (
    'cms.djangoapps.contentstore.rest_api.v1.views.course_details.update_course_details'
)

TEST_COURSE_ID = 'course-v1:org+course+run'


class TestCourseDetailsViewSetPermissions(APITestCase):
    """
    ADR 0028 – permission regression tests for CourseDetailsViewSet.

    Verifies that IsAuthenticated + HasStudioReadAccess enforce the same access
    rules as the deprecated CourseDetailsView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            'cms.djangoapps.contentstore:v1:course_details-detail',
            kwargs={'course_id': TEST_COURSE_ID},
        )

    # --- Unauthenticated ---

    def test_unauthenticated_get_returns_401(self):
        """Unauthenticated GET must return 401 (IsAuthenticated)."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_put_returns_401(self):
        """Unauthenticated PUT must return 401 (IsAuthenticated)."""
        response = self.client.put(self.url, data={}, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- Authenticated but no studio access ---

    def test_non_staff_get_returns_403(self):
        """Authenticated user without studio access must receive 403 on GET (HasStudioReadAccess)."""
        user = UserFactory.create(is_staff=False)
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_staff_put_returns_403(self):
        """Authenticated user without studio access must receive 403 on PUT (HasStudioReadAccess)."""
        user = UserFactory.create(is_staff=False)
        self.client.force_authenticate(user=user)
        response = self.client.put(self.url, data={}, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestCourseDetailsViewSetActions(APITestCase):
    """
    Action tests for CourseDetailsViewSet (retrieve and update).

    Uses a global-staff user (is_staff=True) so HasStudioReadAccess passes
    without needing a real course in the DB.  All service-layer calls are mocked.

    patch.object is used instead of string-based patch() because:
      1. CourseOverview.course_exists falls through to modulestore().has_course() when no
         CourseOverview row exists in the test DB — patch.object reliably replaces the
         classmethod before any code path can reach MongoDB.
      2. serializer_class is stored as a class attribute reference at class-definition time,
         so patching the module-level name has no effect; patch.object on the ViewSet class
         replaces the live attribute directly.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=self.user)
        self.url = reverse(
            'cms.djangoapps.contentstore:v1:course_details-detail',
            kwargs={'course_id': TEST_COURSE_ID},
        )

    @patch.object(CourseDetailsViewSet, 'serializer_class')
    @patch.object(CourseOverview, 'course_exists', return_value=True)
    @patch(MOCK_FETCH)
    def test_retrieve_calls_course_details_fetch(self, mock_fetch, mock_exists, mock_ser_cls):
        """GET calls CourseDetails.fetch() and returns 200."""
        mock_fetch.return_value = MagicMock()
        mock_ser_cls.return_value.data = {'course_id': 'run'}

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_fetch.assert_called_once()

    @patch.object(CourseDetailsViewSet, 'serializer_class')
    @patch.object(CourseOverview, 'course_exists', return_value=True)
    @patch(MOCK_UPDATE)
    @patch(MOCK_MODULESTORE)
    def test_update_calls_update_course_details(
        self, mock_store, mock_update, mock_exists, mock_ser_cls
    ):
        """PUT calls update_course_details() and returns 200."""
        mock_store.return_value.get_course.return_value = MagicMock()
        mock_update.return_value = MagicMock()
        mock_ser_cls.return_value.data = {'course_id': 'run'}

        response = self.client.put(self.url, data={}, content_type='application/json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_update.assert_called_once()
