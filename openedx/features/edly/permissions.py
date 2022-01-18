"""
Custom permissions for edly.
"""
from rest_framework.permissions import BasePermission


class CanAccessEdxAPI(BasePermission):
    """
    Checks if a user can access Edx API.
    """

    def has_permission(self, request, view):
        return request.user.is_staff or request.user.edly_profile.edly_sub_organizations.filter(
            lms_site=request.site
        ).exists()
