"""
Contants used by colaraz features app.
"""
from student.roles import CourseStaffRole, CourseInstructorRole, OrgRoleManagerRole, CourseCreatorRole

ALL_ORGANIZATIONS_MARKER = '__all__'
EMPTY_OPTION = ('', '')
LMS_ADMIN_ROLE = 'lms_admin'
ROLES_FOR_LMS_ADMIN = [
    CourseStaffRole.ROLE,
    CourseInstructorRole.ROLE,
    OrgRoleManagerRole.ROLE,
    CourseCreatorRole.ROLE
]
