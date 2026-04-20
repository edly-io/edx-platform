"""
Unit tests for course details views.
"""
import json
from unittest.mock import patch

import ddt
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.tests.factories import UserFactory

from ...mixins import PermissionAccessMixin


@ddt.ddt
class CourseDetailsViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseDetailsView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_details",
            kwargs={"course_id": self.course.id},
        )

    def test_put_permissions_unauthenticated(self):
        """
        Test that an error is returned in the absence of auth credentials.
        """
        self.client.logout()
        response = self.client.put(self.url)
        error = self.get_and_check_developer_response(response)
        self.assertEqual(error, "Authentication credentials were not provided.")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_put_permissions_unauthorized(self):
        """
        Test that an error is returned if the user is unauthorised.
        """
        client, _ = self.create_non_staff_authed_user_client()
        response = client.put(self.url)
        error = self.get_and_check_developer_response(response)
        self.assertEqual(error, "You do not have permission to perform this action.")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_PREREQUISITE_COURSES": True})
    def test_put_invalid_pre_requisite_course(self):
        pre_requisite_course_keys = [str(self.course.id), "invalid_key"]
        request_data = {"pre_requisite_courses": pre_requisite_course_keys}
        response = self.client.put(
            path=self.url,
            data=json.dumps(request_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["error"], "Invalid prerequisite course key")

    def test_put_course_details(self):
        request_data = {
            "about_sidebar_html": "",
            "banner_image_name": "images_course_image.jpg",
            "banner_image_asset_path": "/asset-v1:edX+E2E-101+course+type@asset+block@images_course_image.jpg",
            "certificate_available_date": "2029-01-02T00:00:00Z",
            "certificates_display_behavior": "end",
            "course_id": "E2E-101",
            "course_image_asset_path": "/static/studio/images/pencils.jpg",
            "course_image_name": "bar_course_image_name",
            "description": "foo_description",
            "duration": "",
            "effort": None,
            "end_date": "2023-08-01T01:30:00Z",
            "enrollment_end": "2023-05-30T01:00:00Z",
            "enrollment_start": "2023-05-29T01:00:00Z",
            "entrance_exam_enabled": "",
            "entrance_exam_id": "",
            "entrance_exam_minimum_score_pct": "50",
            "intro_video": None,
            "language": "creative-commons: ver=4.0 BY NC ND",
            "learning_info": ["foo", "bar"],
            "license": "creative-commons: ver=4.0 BY NC ND",
            "org": "edX",
            "overview": '<section class="about"></section>',
            "pre_requisite_courses": [],
            "run": "course",
            "self_paced": None,
            "short_description": "",
            "start_date": "2023-06-01T01:30:00Z",
            "subtitle": "",
            "syllabus": None,
            "title": "",
            "video_thumbnail_image_asset_path": "/asset-v1:edX+E2E-101+course+type@asset+block@images_course_image.jpg",
            "video_thumbnail_image_name": "images_course_image.jpg",
            "instructor_info": {
                "instructors": [
                    {
                        "name": "foo bar",
                        "title": "title",
                        "organization": "org",
                        "image": "image",
                        "bio": "",
                    }
                ]
            },
        }
        response = self.client.put(
            path=self.url,
            data=json.dumps(request_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CourseDetailsViewPermissionsTest(CourseTestCase):
    """
    ADR 0026 – permission regression tests for CourseDetailsView.

    Verifies that permission_classes = (IsAuthenticated, HasStudioReadAccess) enforces
    the same access rules previously split between @view_auth_classes(is_authenticated=True)
    and the inline has_studio_read_access() checks in get() and put().
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_details",
            kwargs={"course_id": self.course.id},
        )

    # --- Unauthenticated ---
    def test_unauthenticated_get_returns_401(self):
        """
        Unauthenticated GET must return 401.

        Before ADR 0026: enforced by @view_auth_classes(is_authenticated=True).
        After ADR 0026: enforced by IsAuthenticated in permission_classes.
        """
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_put_returns_401(self):
        """
        Unauthenticated PUT must return 401.

        Before ADR 0026: enforced by @view_auth_classes(is_authenticated=True).
        After ADR 0026: enforced by IsAuthenticated in permission_classes.
        """
        self.client.logout()
        response = self.client.put(self.url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- Authenticated but no course access ---
    def test_non_staff_get_returns_403(self):
        """
        Authenticated user without course staff role must receive 403 on GET.

        Before ADR 0026: enforced by inline has_studio_read_access() check in get().
        After ADR 0026: enforced by HasStudioReadAccess in permission_classes.
        """
        client, _ = self.create_non_staff_authed_user_client()
        response = client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_staff_put_returns_403(self):
        """
        Authenticated user without course staff role must receive 403 on PUT.

        Before ADR 0026: enforced by inline has_studio_read_access() check in put().
        After ADR 0026: enforced by HasStudioReadAccess in permission_classes.
        """
        client, _ = self.create_non_staff_authed_user_client()
        response = client.put(self.url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # --- Authorized ---
    def test_course_staff_get_returns_200(self):
        """Course staff/instructor must receive 200 on GET."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
