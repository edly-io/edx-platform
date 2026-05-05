"""
Unit tests for home page view.
"""

from collections import OrderedDict
from datetime import datetime, timedelta

import ddt
import pytz
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.test import APITestCase as DRFAPITestCase  # noqa: E402

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory


@ddt.ddt
class HomePageCoursesViewV2Test(CourseTestCase):
    """
    Tests for HomePageView view version 2.
    """

    def setUp(self):
        super().setUp()
        self.api_v2_url = reverse("cms.djangoapps.contentstore:v2:courses")
        self.api_v1_url = reverse("cms.djangoapps.contentstore:v1:courses")
        self.active_course = CourseOverviewFactory.create(
            id=self.course.id,
            org=self.course.org,
            display_name=self.course.display_name,
        )
        archived_course_key = self.store.make_course_key('demo-org', 'demo-number', 'demo-run')
        self.archived_course = CourseOverviewFactory.create(
            display_name="Demo Course (Sample)",
            id=archived_course_key,
            org=archived_course_key.org,
            end=(datetime.now() - timedelta(days=365)).replace(tzinfo=pytz.UTC),
        )
        self.non_staff_client, _ = self.create_non_staff_authed_user_client()

    def test_home_page_response(self):
        """Get list of courses available to the logged in user.

        Expected result:
        - A paginated response.
        - A list of courses available to the logged in user.
        """
        response = self.client.get(self.api_v2_url)
        course_id = str(self.course.id)
        archived_course_id = str(self.archived_course.id)

        expected_data = {
            "courses": [
                OrderedDict([
                    ("course_key", course_id),
                    ("display_name", self.course.display_name),
                    ("lms_link", f'{settings.LMS_ROOT_URL}/courses/{course_id}/jump_to/{self.course.location}'),
                    ("cms_link", f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.course.id)}'),
                    ("number", self.course.number),
                    ("org", self.course.org),
                    ("rerun_link", f'/course_rerun/{course_id}'),
                    ("run", self.course.id.run),
                    ("url", f'/course/{course_id}'),
                    ("is_active", True),
                ]),
                OrderedDict([
                    ("course_key", str(self.archived_course.id)),
                    ("display_name", self.archived_course.display_name),
                    (
                        "lms_link",
                        f'{settings.LMS_ROOT_URL}/courses/{archived_course_id}/jump_to/{self.archived_course.location}'
                    ),
                    (
                        "cms_link",
                        f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.archived_course.id)}',
                    ),
                    ("number", self.archived_course.number),
                    ("org", self.archived_course.org),
                    ("rerun_link", f'/course_rerun/{str(self.archived_course.id)}'),
                    ("run", self.archived_course.id.run),
                    ("url", f'/course/{str(self.archived_course.id)}'),
                    ("is_active", False),
                ]),
            ],
            "in_process_course_actions": [],
        }
        expected_response = {
            'count': 2,
            'num_pages': 1,
            'current_page': 1,
            'start': 0,
            'next': None,
            'previous': None,
            'results': expected_data,
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.data)

    def test_active_only_query_if_passed(self):
        """Get list of active courses only.

        Expected result:
        - A list of active courses available to the logged in user.
        """
        response = self.client.get(self.api_v2_url, {"active_only": "true"})

        self.assertEqual(len(response.data["results"]["courses"]), 1)
        self.assertEqual(response.data["results"]["courses"], [OrderedDict([
            ("course_key", str(self.course.id)),
            ("display_name", self.course.display_name),
            ("lms_link", f'{settings.LMS_ROOT_URL}/courses/{str(self.course.id)}/jump_to/{self.course.location}'),
            ("cms_link", f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.course.id)}'),
            ("number", self.course.number),
            ("org", self.course.org),
            ("rerun_link", f'/course_rerun/{str(self.course.id)}'),
            ("run", self.course.id.run),
            ("url", f'/course/{str(self.course.id)}'),
            ("is_active", True),
        ])])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_archived_only_query_if_passed(self):
        """Get list of archived courses only.

        Expected result:
        - A list of archived courses available to the logged in user.
        """
        response = self.client.get(self.api_v2_url, {"archived_only": "true"})

        self.assertEqual(len(response.data["results"]["courses"]), 1)
        self.assertEqual(response.data["results"]["courses"], [OrderedDict([
            ("course_key", str(self.archived_course.id)),
            ("display_name", self.archived_course.display_name),
            (
                "lms_link",
                '{url_root}/courses/{course_id}/jump_to/{location}'.format(
                    url_root=settings.LMS_ROOT_URL,
                    course_id=str(self.archived_course.id),
                    location=self.archived_course.location
                ),
            ),
            ("cms_link", f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.archived_course.id)}'),
            ("number", self.archived_course.number),
            ("org", self.archived_course.org),
            ("rerun_link", f'/course_rerun/{str(self.archived_course.id)}'),
            ("run", self.archived_course.id.run),
            ("url", f'/course/{str(self.archived_course.id)}'),
            ("is_active", False),
        ])])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_query_if_passed(self):
        """Get list of courses when search filter passed as a query param.

        Expected result:
        - A list of courses (active or inactive) available to the logged in user for the specified search.
        """
        response = self.client.get(self.api_v2_url, {"search": "sample"})

        self.assertEqual(len(response.data["results"]["courses"]), 1)
        self.assertEqual(response.data["results"]["courses"], [OrderedDict([
            ("course_key", str(self.archived_course.id)),
            ("display_name", self.archived_course.display_name),
            (
                "lms_link",
                '{url_root}/courses/{course_id}/jump_to/{location}'.format(
                    url_root=settings.LMS_ROOT_URL,
                    course_id=str(self.archived_course.id),
                    location=self.archived_course.location
                ),
            ),
            ("cms_link", f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.archived_course.id)}'),
            ("number", self.archived_course.number),
            ("org", self.archived_course.org),
            ("rerun_link", f'/course_rerun/{str(self.archived_course.id)}'),
            ("run", self.archived_course.id.run),
            ("url", f'/course/{str(self.archived_course.id)}'),
            ("is_active", False),
        ])])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_order_query_if_passed(self):
        """Get list of courses when order filter passed as a query param.

        Expected result:
        - A list of courses (active or inactive) available to the logged in user for the specified order.
        """
        response = self.client.get(self.api_v2_url, {"order": "org"})

        self.assertEqual(len(response.data["results"]["courses"]), 2)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"]["courses"][0]["org"], "demo-org")

    def test_page_query_if_passed(self):
        """Get list of courses when page filter passed as a query param.

        Expected result:
        - A list of courses (active or inactive) available to the logged in user for the specified page.
        """
        response = self.client.get(self.api_v2_url, {"page": 1})

        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @ddt.data(
        ("active_only", "true"),
        ("archived_only", "true"),
        ("search", "sample"),
        ("order", "org"),
        ("page", 1),
    )
    @ddt.unpack
    def test_if_empty_list_of_courses(self, query_param, value):
        """Get list of courses when no courses are available.

        Expected result:
        - An empty list of courses available to the logged in user.
        """
        self.active_course.delete()
        self.archived_course.delete()

        response = self.client.get(self.api_v2_url, {query_param: value})

        self.assertEqual(len(response.data['results']['courses']), 0)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @ddt.data(
        ("active_only", "true", 2, 0),
        ("archived_only", "true", 0, 1),
        ("search", "foo", 1, 0),
        ("search", "demo", 0, 1),
        ("order", "org", 2, 1),
        ("order", "display_name", 2, 1),
        ("order", "number", 2, 1),
        ("order", "run", 2, 1)
    )
    @ddt.unpack
    def test_filter_and_ordering_courses(
        self,
        filter_key,
        filter_value,
        expected_active_length,
        expected_archived_length
    ):
        """Get list of courses when filter and ordering are applied.

        This test creates two courses besides the default courses created in the setUp method.
        Then filters and orders them based on the filter_key and filter_value passed as query parameters.

        Expected result:
        - A list of courses available to the logged in user for the specified filter and order.
        """
        archived_course_key = self.store.make_course_key("demo-org", "demo-number", "demo-run")
        CourseOverviewFactory.create(
            display_name="Course (Demo)",
            id=archived_course_key,
            org=archived_course_key.org,
            end=(datetime.now() - timedelta(days=365)).replace(tzinfo=pytz.UTC),
        )
        active_course_key = self.store.make_course_key("foo-org", "foo-number", "foo-run")
        CourseOverviewFactory.create(
            display_name="Course (Foo)",
            id=active_course_key,
            org=active_course_key.org,
        )

        response = self.client.get(self.api_v2_url, {filter_key: filter_value})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            len([course for course in response.data["results"]["courses"] if course["is_active"]]),
            expected_active_length
        )
        self.assertEqual(
            len([course for course in response.data["results"]["courses"] if not course["is_active"]]),
            expected_archived_length
        )

    @ddt.data(
        ("active_only", "true"),
        ("archived_only", "true"),
        ("search", "sample"),
        ("order", "org"),
        ("page", 1),
    )
    @ddt.unpack
    def test_if_empty_list_of_courses_non_staff(self, query_param, value):
        """Get list of courses when no courses are available for non-staff users.

        Expected result:
        - An empty list of courses available to the logged in user.
        """
        self.active_course.delete()
        self.archived_course.delete()

        response = self.non_staff_client.get(self.api_v2_url, {query_param: value})

        self.assertEqual(len(response.data["results"]["courses"]), 0)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class TestHomePageCoursesPaginatorStructure(TestCase):
    """
    ADR 0032 – structural checks for HomePageCoursesPaginator.

    Pure import-level check: no course data or MongoDB required.
    Verifies the paginator inherits from DefaultPagination, satisfying the
    ADR 0032 requirement that all list endpoints use the standard paginator.
    """

    def test_paginator_is_defaultpagination_subclass(self):
        """HomePageCoursesPaginator must subclass DefaultPagination (not PageNumberPagination directly)."""
        from edx_rest_framework_extensions.paginators import DefaultPagination
        from cms.djangoapps.contentstore.rest_api.v2.views.home import HomePageCoursesPaginator
        self.assertTrue(
            issubclass(HomePageCoursesPaginator, DefaultPagination),
            "ADR 0032: HomePageCoursesPaginator must subclass DefaultPagination",
        )


class TestHomePageCoursesViewV2PaginationEnvelope(DRFAPITestCase):
    """
    ADR 0032 – pagination envelope regression tests for HomePageCoursesViewV2.

    Uses a plain staff user (no CourseFactory / MongoDB) so that the test can
    run in any environment.  The endpoint returns an empty course list for a
    user with no courses, which is sufficient to verify the 7-field envelope.

    Verifies that the GET /api/contentstore/v2/home/courses endpoint returns
    the full 7-field ADR 0032 response envelope: count, num_pages, current_page,
    start, next, previous, results.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=self.user)
        self.url = reverse("cms.djangoapps.contentstore:v2:courses")

    def test_response_includes_full_envelope(self):
        """All 7 ADR 0032 envelope fields must be present in every paginated response."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for field in ('count', 'num_pages', 'current_page', 'start', 'next', 'previous', 'results'):
            self.assertIn(field, response.data, f"ADR 0032: missing envelope field '{field}'")

    def test_current_page_is_one_on_first_page(self):
        """current_page must equal 1 when requesting the first page."""
        response = self.client.get(self.url, {'page': 1})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['current_page'], 1)

    def test_start_is_zero_on_first_page(self):
        """start must be 0 on the first page (0-based index of the first item)."""
        response = self.client.get(self.url, {'page': 1})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['start'], 0)

    def test_results_contains_courses_key(self):
        """results must be a dict containing the 'courses' key."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data['results'], dict)
        self.assertIn('courses', response.data['results'])


class HomePageCoursesViewV2PermissionsTest(TestCase):
    """
    ADR 0026 – permission regression tests for HomePageCoursesViewV2.

    Verifies that the explicit permission_classes = (IsAuthenticated,) enforces
    the same access rules previously set by the @view_auth_classes(is_authenticated=True)
    decorator.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse("cms.djangoapps.contentstore:v2:courses")
        self.user = UserFactory.create()
        self.staff_user = UserFactory.create(is_staff=True)

    def test_unauthenticated_request_returns_401(self):
        """
        Unauthenticated request (no credentials) must be rejected with 401.

        Before ADR 0026: enforced by @view_auth_classes(is_authenticated=True).
        After ADR 0026: enforced by permission_classes = (IsAuthenticated,).
        """
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_gets_200(self):
        """
        Any authenticated user (not necessarily staff) must receive 200.

        HomePageCoursesViewV2 only requires authentication — no staff role needed.
        The view returns an empty course list for users with no assigned courses.
        """
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_user_gets_200(self):
        """Staff user must also receive 200 (staff is a superset of authenticated)."""
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_post_by_unauthenticated_returns_401(self):
        """Non-GET methods also enforce authentication — POST without credentials is 401."""
        response = self.client.post(self.url, data={})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
