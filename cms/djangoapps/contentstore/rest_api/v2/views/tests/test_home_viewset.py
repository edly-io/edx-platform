"""
Unit tests for HomeCoursesViewSetV2 (ADR 0028 ViewSet migration).

All service-layer calls are mocked so these tests run without MongoDB.
"""
from unittest.mock import MagicMock, patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from cms.djangoapps.contentstore.rest_api.v2.views.home import HomeCoursesViewSetV2
from common.djangoapps.student.tests.factories import UserFactory

MOCK_GET_COURSE_CONTEXT_V2 = (
    'cms.djangoapps.contentstore.rest_api.v2.views.home.get_course_context_v2'
)


class TestHomeCoursesViewSetV2Permissions(APITestCase):
    """
    ADR 0028 – permission regression tests for HomeCoursesViewSetV2.

    URL: GET /api/contentstore/v2/home/courses/  (router name: home-courses-list)
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse("cms.djangoapps.contentstore:v2:home-courses-list")

    def test_unauthenticated_list_returns_401(self):
        """Unauthenticated request must be rejected with 401."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_gets_200(self):
        """Any authenticated user gets 200 — only IsAuthenticated is required."""
        user = UserFactory.create()
        self.client.force_authenticate(user=user)
        with patch(MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_user_gets_200(self):
        """Staff user also gets 200 (superset of authenticated)."""
        user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=user)
        with patch(MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class TestHomeCoursesViewSetV2Actions(APITestCase):
    """
    ADR 0028 – action tests for HomeCoursesViewSetV2.list.

    Service layer (get_course_context_v2) and serializer are mocked to keep
    these tests free of MongoDB and CourseOverview DB queries.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=self.user)
        self.url = reverse("cms.djangoapps.contentstore:v2:home-courses-list")

    @patch.object(HomeCoursesViewSetV2, 'get_serializer')
    @patch(MOCK_GET_COURSE_CONTEXT_V2)
    def test_list_calls_get_course_context_v2(self, mock_context, mock_get_ser):
        """GET /home/courses/ calls get_course_context_v2 exactly once and returns 200."""
        mock_context.return_value = ([], [])
        mock_get_ser.return_value.data = {
            'courses': [],
            'in_process_course_actions': [],
        }

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_context.assert_called_once()
        # Response must be paginated (count / num_pages / next / previous / results)
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)
