"""
URLs for the Enrollment API

"""

from django.conf import settings
from django.urls import path, re_path
from rest_framework.routers import DefaultRouter

from .views import (
    CourseEnrollmentsApiListView,
    EnrollmentAllowedView,
    EnrollmentCourseDetailView,
    EnrollmentListView,
    EnrollmentUserRolesView,
    EnrollmentView,
    EnrollmentViewSet,
    UnenrollmentView,
)

# ADR 0028: EnrollmentViewSet registered via DefaultRouter.
# Generates: GET/POST /enrollment/, POST /enrollment/unenroll/, GET/POST/DELETE /enrollment/enrollment_allowed/
router = DefaultRouter()
router.register(r"enrollment", EnrollmentViewSet, basename="enrollment")

urlpatterns = router.urls + [
    # EnrollmentView kept as-is: non-standard {username},{course_key} URL is incompatible with
    # DefaultRouter lookup — migrate to ViewSet retrieve() in a follow-up (TODO ADR 0028).
    re_path(
        r"^enrollment/{username},{course_key}$".format(
            username=settings.USERNAME_PATTERN, course_key=settings.COURSE_ID_PATTERN
        ),
        EnrollmentView.as_view(),
        name="courseenrollment",
    ),
    re_path(rf"^enrollment/{settings.COURSE_ID_PATTERN}$", EnrollmentView.as_view(), name="courseenrollment"),
    re_path(r"^enrollments/?$", CourseEnrollmentsApiListView.as_view(), name="courseenrollmentsapilist"),
    re_path(
        rf"^course/{settings.COURSE_ID_PATTERN}$", EnrollmentCourseDetailView.as_view(), name="courseenrollmentdetails"
    ),
    path("roles/", EnrollmentUserRolesView.as_view(), name="roles"),

    # DEPRECATED (ADR 0028): flat URL patterns kept for backward compatibility.
    # Will be removed after one named release. Use the router-generated enrollment/ URLs instead.
    path("enrollment", EnrollmentListView.as_view(), name="courseenrollments"),
    path("unenroll/", UnenrollmentView.as_view(), name="unenrollment"),
    path("enrollment_allowed/", EnrollmentAllowedView.as_view(), name="courseenrollmentallowed"),
]
