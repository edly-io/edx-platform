"""
Custom permissions for the content store views.
"""

from rest_framework.permissions import BasePermission

from common.djangoapps.student.auth import has_studio_read_access, has_studio_write_access
from openedx.core.lib.api.view_utils import validate_course_key


class HasStudioWriteAccess(BasePermission):
    """
    Check if the user has write access to studio.
    """

    def has_permission(self, request, view):
        """
        Check if the user has write access to studio.
        """
        course_key_string = view.kwargs.get("course_key_string")
        course_key = validate_course_key(course_key_string)
        return has_studio_write_access(request.user, course_key)


class HasStudioReadAccess(BasePermission):
    """
    Check if the user has read access to studio for the requested course.

    ADR 0026: replaces inline ``if not has_studio_read_access(request.user, course_key):
    self.permission_denied(request)`` checks previously embedded in view methods
    (e.g. CourseDetailsView.get() and CourseDetailsView.put()).

    Expects the view to receive the course identifier as a ``course_id`` URL kwarg.
    """

    def has_permission(self, request, view):
        course_key_string = view.kwargs.get("course_id")
        course_key = validate_course_key(course_key_string)
        return has_studio_read_access(request.user, course_key)
