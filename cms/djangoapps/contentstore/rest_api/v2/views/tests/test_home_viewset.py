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


class TestHomeCoursesViewSetV2OrderingDeprecation(APITestCase):
    """
    ADR 0033 – sorting standardization tests.

    Verify that:
      * The new ``ordering`` parameter works and does NOT trigger the
        ``Deprecation`` header.
      * The legacy ``order`` parameter is still accepted (backward compat,
        ADR 0033 BC strategy §1) but DOES trigger the ``Deprecation`` header
        (BC strategy §2).
      * When both are sent, ``ordering`` wins (its value is forwarded into
        ``get_query_params_if_present``) and the ``Deprecation`` header is
        still emitted because ``order`` was present in the query string.
      * When neither is sent, no ``Deprecation`` header is emitted.
    """

    EXPECTED_DEPRECATION_HEADER = (
        "Parameter 'order' is deprecated. Use 'ordering' instead. "
        "Support will be removed in release '<release_name>'."
    )

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=self.user)
        self.url = reverse("cms.djangoapps.contentstore:v2:home-courses-list")

    def _patched_get_course_context(self, captured):
        """
        Returns a MOCK_GET_COURSE_CONTEXT_V2 patch whose side_effect captures
        the request so the test can assert which ordering value reached the
        service layer via ``get_query_params_if_present``.
        """
        def _capture(request):
            captured['request'] = request
            return ([], [])
        return patch(MOCK_GET_COURSE_CONTEXT_V2, side_effect=_capture)

    def test_new_ordering_param_does_not_emit_deprecation_header(self):
        """``?ordering=display_name`` returns 200 and no ``Deprecation`` header."""
        with patch(MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.url, {'ordering': 'display_name'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('Deprecation', response.headers)

    def test_legacy_order_param_emits_deprecation_header(self):
        """``?order=display_name`` returns 200 AND emits the ADR 0033 header."""
        with patch(MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.url, {'order': 'display_name'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.headers.get('Deprecation'), self.EXPECTED_DEPRECATION_HEADER)

    def test_ordering_wins_when_both_sent_but_header_still_emitted(self):
        """
        When both params are present, ``get_query_params_if_present`` must
        forward the value of ``ordering`` (not ``order``) to the service
        layer, but the ``Deprecation`` header must still be emitted because
        the request *contained* the deprecated param.
        """
        captured = {}
        with self._patched_get_course_context(captured):
            response = self.client.get(
                self.url,
                {'ordering': 'display_name', 'order': 'org'},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.headers.get('Deprecation'), self.EXPECTED_DEPRECATION_HEADER)
        # Confirm ordering wins inside get_query_params_if_present
        from cms.djangoapps.contentstore.views.course import get_query_params_if_present
        _search, order_resolved, _active, _archived = get_query_params_if_present(captured['request'])
        self.assertEqual(order_resolved, 'display_name')

    def test_no_ordering_param_no_deprecation_header(self):
        """Plain ``GET /home/courses/`` does not emit the ``Deprecation`` header."""
        with patch(MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('Deprecation', response.headers)
