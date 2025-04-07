"""
Custom permissions for edly.
"""
from django.conf import settings
from rest_framework.permissions import BasePermission

from edly_panel_app.api.v1.constants import WORDPRESS_WORKER_USER
from openedx.features.edly.utils import get_edly_sub_org_from_request, user_has_edly_organization_access
from openedx.features.edly.constants import EDLY_PANEL_WORKER_USER


class CanAccessEdxAPI(BasePermission):
    """
    Checks if a user can access Edx API.
    """

    def has_permission(self, request, view):
        api_key = getattr(settings, "EDX_API_KEY", None)
        if api_key is not None and request.META.get("HTTP_X_EDX_API_KEY") == api_key:
            return True

        edly_access_user = request.user.edly_multisite_user.filter(
            sub_org__lms_site=request.site
        )
        return request.user.is_staff or bool(edly_access_user)


class IsWpAdminUser(BasePermission):
    """
    Checks if a user is a wordpress admin user.
    """

    def has_permission(self, request, view):
        sub_org = get_edly_sub_org_from_request(request)
        is_edly_access_user = request.user.edly_multisite_user.filter(
            sub_org=sub_org,
            groups__name=settings.EDLY_WP_ADMIN_USERS_GROUP
        ).exists()
        has_edly_user_access = user_has_edly_organization_access(
            request) and (request.user.is_superuser or is_edly_access_user)
        return has_edly_user_access or request.user.username == WORDPRESS_WORKER_USER


class CanAccessSiteDeletion(BasePermission):
    """
    Checks if a user has the access to delete a site.
    """

    def has_permission(self, request, view):
        """
        Checks for user's permission for current site.
        """
        return request.user.is_staff or request.user.username == EDLY_PANEL_WORKER_USER
