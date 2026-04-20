"""
Custom permissions for the content store views.
"""

from rest_framework.permissions import BasePermission

from common.djangoapps.student.auth import has_course_author_access, has_studio_write_access
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


class HasCourseAuthorAccess(BasePermission):
    """
    Check if the user has course author access.

    ADR 0026: replaces the ``@course_author_access_required`` method decorator.
    Expects a ``course_id`` URL kwarg.
    """

    def has_permission(self, request, view):
        """
        Check if the user has course author access.
        """
        course_key_string = view.kwargs.get("course_id")
        course_key = validate_course_key(course_key_string)
        return has_course_author_access(request.user, course_key)
