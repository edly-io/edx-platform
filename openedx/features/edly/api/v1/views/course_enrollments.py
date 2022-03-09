"""
Views for course enrollments API
"""
from figures.views import CourseEnrollmentViewSet

from openedx.core.lib.api.permissions import ApiKeyHeaderPermissionIsAuthenticated


class EdlyCourseEnrollmentViewSet(CourseEnrollmentViewSet):
    """
    **Use Case**

        Get information about the course enrollments about a specific course.

    **Example Request**

        GET /api/v1/courses/course_enrollment/

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.
    """
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)
