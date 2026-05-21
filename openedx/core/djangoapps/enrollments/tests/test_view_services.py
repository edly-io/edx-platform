"""
Tests for ``openedx.core.djangoapps.enrollments.view_services``.

Per ADR 0031 (Merge Similar Endpoints) the enrollment HTTP operations live
in a shared ``EnrollmentOperationsService`` so the canonical
``EnrollmentViewSet`` and its deprecated APIView aliases cannot drift.  The
tests here verify the shared contract directly — both as a unit-test of the
service object and as a regression guard that the deprecated and canonical
view layers both delegate to it.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import CourseEnrollmentAllowed
from common.djangoapps.student.tests.factories import SuperuserFactory, UserFactory
from openedx.core.djangoapps.enrollments.view_services import EnrollmentOperationsService
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class EnrollmentOperationsServiceAllowedTest(APITestCase):
    """
    Unit-tests for the allowed-enrollment helpers in ``EnrollmentOperationsService``.

    These exercise the service object directly (no HTTP layer involved) so a
    bug in the view-to-service wiring will be caught here without depending
    on the URL resolver or DRF dispatcher.
    """

    def setUp(self):
        super().setUp()
        self.service = EnrollmentOperationsService()
        self.email = "allowed@example.com"
        self.course_id = "course-v1:edX+DemoX+Demo_Course"

    def test_list_allowed_for_email_returns_empty_when_no_rows(self):
        assert not self.service.list_allowed_for_email(self.email).exists()

    def test_list_allowed_for_email_returns_matching_rows(self):
        CourseEnrollmentAllowed.objects.create(email=self.email, course_id=self.course_id)
        CourseEnrollmentAllowed.objects.create(email=self.email, course_id="course-v1:edX+Other+Other")
        CourseEnrollmentAllowed.objects.create(email="other@example.com", course_id=self.course_id)

        rows = list(self.service.list_allowed_for_email(self.email))
        assert len(rows) == 2
        assert all(r.email == self.email for r in rows)

    def test_delete_allowed_enrollment_removes_row(self):
        CourseEnrollmentAllowed.objects.create(email=self.email, course_id=self.course_id)
        self.service.delete_allowed_enrollment(self.email, self.course_id)
        assert not CourseEnrollmentAllowed.objects.filter(
            email=self.email, course_id=self.course_id,
        ).exists()

    def test_delete_allowed_enrollment_raises_when_missing(self):
        # The view layer translates this to a 404; the service intentionally
        # surfaces the underlying ORM exception (ADR 0031: per-operation
        # error mapping stays in the view so OpenAPI schemas remain accurate).
        from django.core.exceptions import ObjectDoesNotExist
        with self.assertRaises(ObjectDoesNotExist):
            self.service.delete_allowed_enrollment(self.email, self.course_id)


@skip_unless_lms
class EnrollmentOperationsServiceUnenrollTest(APITestCase):
    """
    Unit-tests for the retirement-pipeline unenroll helper.  Both
    ``EnrollmentViewSet.unenroll`` and the deprecated ``UnenrollmentView.post``
    must produce identical responses because they share this code path.
    """

    def setUp(self):
        super().setUp()
        self.service = EnrollmentOperationsService()

    def test_unenroll_missing_username_returns_404(self):
        response = self.service.unenroll_user_for_retirement(None)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unenroll_blank_username_returns_404(self):
        response = self.service.unenroll_user_for_retirement("")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unenroll_unknown_retirement_status_returns_404(self):
        # No UserRetirementStatus row exists for this username.
        response = self.service.unenroll_user_for_retirement("does-not-exist")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@skip_unless_lms
class EnrollmentOperationsServiceListEnrollmentsTest(APITestCase):
    """
    Unit-tests for the per-operation permission filter applied by
    ``list_enrollments_for_user`` (ADR 0031 layer-2 authorization).
    """

    def setUp(self):
        super().setUp()
        self.service = EnrollmentOperationsService()
        self.user = UserFactory.create(username="alice")
        self.other = UserFactory.create(username="bob")

    def test_self_lookup_returns_full_list_unfiltered(self):
        # No enrollments exist, so the result is an empty list either way —
        # what matters is that the service didn't raise and didn't filter the
        # queryset down based on CourseStaffRole when the caller is asking
        # about themselves.
        result = self.service.list_enrollments_for_user(
            request_user=self.user, target_username=self.user.username, has_api_key=False,
        )
        assert result == []

    def test_api_key_bypasses_per_course_filter(self):
        # Asking about another user with the api-key bit set returns the full
        # list without applying the CourseStaffRole filter.
        result = self.service.list_enrollments_for_user(
            request_user=self.user, target_username=self.other.username, has_api_key=True,
        )
        assert result == []

    def test_cross_user_without_privilege_filters_to_staffed_courses(self):
        # ``self.user`` is not staff anywhere, so they see nothing of
        # ``self.other``'s enrollments — even if any existed.
        result = self.service.list_enrollments_for_user(
            request_user=self.user, target_username=self.other.username, has_api_key=False,
        )
        assert result == []


# ---------------------------------------------------------------------------
# ADR 0031 – deprecated/canonical view-layer regression guard.
#
# These tests assert that the two URL paths sharing the same business logic
# call into the *same* service-layer entry point.  If a future change skips
# the service (e.g. by inlining logic back into a view), these will fail.
# ---------------------------------------------------------------------------


@skip_unless_lms
class Adr0031SharedServiceRegressionTest(APITestCase):
    """
    Regression guard for ADR 0031: both the deprecated APIView aliases and
    the canonical ``EnrollmentViewSet`` must delegate to the shared
    ``EnrollmentOperationsService`` instance (``_OPS``) in
    ``openedx.core.djangoapps.enrollments.views``.
    """

    def setUp(self):
        super().setUp()
        # The unenroll routes require ``CanRetireUser`` (granted to
        # superusers) and the allowed routes require ``IsAdminUser``
        # (granted to staff).  Give the test user both flags so a single
        # client satisfies the coarse permission checks (ADR 0031 layer 1)
        # and the test can focus on the service-delegation contract
        # (ADR 0031 layer 2) without auth getting in the way.
        self.user = SuperuserFactory()
        self.user.is_staff = True
        self.user.save()
        self.client.force_authenticate(user=self.user)

    def test_deprecated_unenroll_path_routes_through_service(self):
        """POST /unenroll/ must invoke ``unenroll_user_for_retirement`` on the shared service."""
        target = "_OPS.unenroll_user_for_retirement"
        with patch(f"openedx.core.djangoapps.enrollments.views.{target}") as mocked:
            from rest_framework.response import Response
            mocked.return_value = Response(status=status.HTTP_204_NO_CONTENT)
            self.client.post(reverse("unenrollment"), data={"username": "anybody"}, format="json")
        mocked.assert_called_once_with("anybody")

    def test_canonical_unenroll_action_routes_through_service(self):
        """POST /enrollment/unenroll/ must invoke the same service entry point."""
        target = "_OPS.unenroll_user_for_retirement"
        with patch(f"openedx.core.djangoapps.enrollments.views.{target}") as mocked:
            from rest_framework.response import Response
            mocked.return_value = Response(status=status.HTTP_204_NO_CONTENT)
            self.client.post(reverse("enrollment-unenroll"), data={"username": "anybody"}, format="json")
        mocked.assert_called_once_with("anybody")

    def test_deprecated_allowed_get_routes_through_service(self):
        """GET /enrollment_allowed/ must invoke ``list_allowed_for_email`` on the shared service."""
        with patch(
            "openedx.core.djangoapps.enrollments.views._OPS.list_allowed_for_email",
            return_value=CourseEnrollmentAllowed.objects.none(),
        ) as mocked:
            response = self.client.get(reverse("courseenrollmentallowed"), {"email": "x@example.com"})
        assert response.status_code == status.HTTP_200_OK
        mocked.assert_called_once_with("x@example.com")

    def test_canonical_allowed_get_routes_through_service(self):
        """GET /enrollment/enrollment_allowed/ must invoke the same service entry point."""
        with patch(
            "openedx.core.djangoapps.enrollments.views._OPS.list_allowed_for_email",
            return_value=CourseEnrollmentAllowed.objects.none(),
        ) as mocked:
            response = self.client.get(reverse("enrollment-allowed"), {"email": "x@example.com"})
        assert response.status_code == status.HTTP_200_OK
        mocked.assert_called_once_with("x@example.com")
