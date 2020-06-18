"""
Contants used by colaraz features app.
"""
from student.roles import (
    CourseStaffRole,
    CourseInstructorRole,
    OrgRoleManagerRole,
    CourseCreatorRole
)

ALL_ORGANIZATIONS_MARKER = '__all__'
EMPTY_OPTION = ('', '')
LMS_ADMIN_ROLE = 'lms_admin'
ROLES_FOR_LMS_ADMIN = [
    CourseStaffRole.ROLE,
    CourseInstructorRole.ROLE,
    OrgRoleManagerRole.ROLE,
    CourseCreatorRole.ROLE
]
GLOBAL_ROLES = [
    ('course_creator_group', 'Course Creator'),
]
ORG_ROLES = [
    ('org_instructor', 'Organizational Instructor'),
    ('org_staff', 'Organizational Staff'),
]
COURSE_ROLES = [
    ('finance_admin', 'Finance Admin'),
    ('support', 'Support'),
    ('beta_testers', 'Beta Testers'),
    ('sales_admin', 'Sales Admin'),
    ('library_user', 'Library User'),
    ('instructor', 'Instructor'),
    ('staff', 'Staff'),
]
ALL_ROLES = GLOBAL_ROLES + ORG_ROLES + COURSE_ROLES
COURSE_ACCESS_ROLES_DISPLAY_MAPPING = {
    'org_instructor': 'Organizational Instructor',
    'org_staff': 'Organizational Staff',
    'course_creator_group': 'Course Creator',
    'finance_admin': 'Finance Admin',
    'support': 'Support',
    'beta_testers': 'Beta Testers',
    'sales_admin': 'Sales Admin',
    'library_user': 'Library User',
    'instructor': 'Instructor',
    'staff': 'Staff',
}
