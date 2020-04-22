"""
Permissions related code for Colaraz.
"""
from rest_framework.permissions import IsAdminUser
from opaque_keys.edx.django.models import CourseKeyField
from student.roles import OrgRoleManagerRole


class IsAdminOrOrganizationalRoleManager(IsAdminUser):
    """
    Permission class to allow the user who is either an admin `is_staff=True` or organizational role manager.
    """

    def has_permission(self, request, view):
        """
        Check if the request user has appropriate permission.

        Arguments:
            request (Request): Django request object.
            view (View): object of the class based view.

        Returns:
             (bool): True if request user has permission, False otherwise.
        """
        is_admin = super(IsAdminOrOrganizationalRoleManager, self).has_permission(request, view)

        return is_admin or self.is_organizational_role_manager(request.user)

    @staticmethod
    def is_organizational_role_manager(user):
        """
        Check if request user us organizational role manager.

        Arguments:
            user (User): Django user object.
        Returns:
            (bool): True if request user is organizational role manager, False otherwise.
        """
        return user.courseaccessrole_set.filter(
            role=OrgRoleManagerRole.ROLE,
            course_id=CourseKeyField.Empty
        ).exclude(
            org__exact=''
        ).exists()
