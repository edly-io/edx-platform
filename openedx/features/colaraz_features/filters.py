"""
Filters for Colaraz Views.
"""
from opaque_keys.edx.django.models import CourseKeyField
from student.roles import OrgRoleManagerRole


class CourseAccessRoleFilterMixin(object):
    """
    Filter to queryset based on user's course access roles.

    This Mixin can be used on class based views with `get_queryset` method.
    It applies filters based on the requesting user so that only authorized users are able to view/update/delete.
    """

    def get_queryset(self):
        """
        Override `get_queryset` to apply course access role based filtering.
        """
        queryset = super(CourseAccessRoleFilterMixin, self).get_queryset()
        user = self.request.user

        if user.is_staff:
            return self.apply_global_staff_filter(queryset)

        role_manager_roles = self.get_organizational_roles(user, OrgRoleManagerRole.ROLE)
        if len(role_manager_roles) > 0:
            return self.apply_role_manager_filter(queryset, [role.org for role in role_manager_roles])

        # Empty queryset for all other users
        return queryset.none()

    @staticmethod
    def get_organizational_roles(user, role):
        """
        Get organization roles for the given user.

        Arguments:
            user (User): Django User object to check role permission against.
            role (str): Role against which to check and retrieve CourseAccessRole.

        Returns:
            (list<CourseAccessRole>): Organizational CourseAccessRole for the given user and role, None if not found.
        """
        return user.courseaccessrole_set.filter(
            role=role,
            course_id=CourseKeyField.Empty
        ).exclude(
            org__exact=''
        ).all()

    @staticmethod
    def apply_global_staff_filter(queryset):
        """
        Apply filter on queryset for global staff users.

        Arguments:
            queryset (QuerySet): Django queryset instance for CourseAccessRole model.

        Returns:
             (QuerySet): Django queryset instance for CourseAccessRole model.
        """
        # Staff user can access everything.
        return queryset

    @staticmethod
    def apply_role_manager_filter(queryset, organizations):
        """
        Apply filter on queryset for role manager.

        Arguments:
            queryset (QuerySet): Django queryset instance for CourseAccessRole model.
            organizations (list<str | unicode>): Organization name to apply filter on the queryset.

        Returns:
             (QuerySet): Django queryset instance for CourseAccessRole model.
        """
        # Role manager can only maintain its own organization's access roles.
        return queryset.filter(org__in=organizations)
