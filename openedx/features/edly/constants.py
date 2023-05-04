"""
Constants for Edly feature app.
"""

# PLAN CHOICES
TRIAL = 'trial'
ESSENTIALS = 'essentials'
ELITE = 'elite'
LEGACY = 'legacy'
TRIAL_EXPIRED = 'trial expired'
DEACTIVATED = 'deactivated'

# FEATURE FLAGS
ADDITIONAL_USER_PRICE = 'additional_user_price'
MONTHLY_ACTIVE_USERS = 'monthly_active_users'
NUMBER_OF_REGISTERED_USERS = 'number_of_registered_users'
NUMBER_OF_COURSES = 'number_of_courses'
STAFF_USERS = 'staff_users'
WP_ADMIN_USERS = 'wp_admin_users'
COURSE_AUTHORS = 'course_authors'
PANEL_ADMINS = 'panel_admins'

# EMAIL CONFIGS
ACCOUNT_STATUS = 'account_status'
ACCOUNT_STATUS_DETAIL = 'Notifies a user that they have been removed from a role through the Edly Panel.'
COURSE_ENROLLMENT = 'course_enrollment'
COURSE_ENROLLMENT_DETAIL = 'Course enrollment email is sent to learners when they are enrolled to a course through instructor dashboard in LMS.'
ROLE_ASSIGNED = 'role_assigned'
ROLE_ASSIGNED_DETAIL = 'Notifies a user that they have been assigned a new role (e.g. Admin, Course Creator etc.) through the Edly Panel.'
ROLE_REVOKED = 'role_revoked'
ROLE_REVOKED_DETAIL = 'Notifies a user that they have been removed from a role through the Edly Panel.'
SUBSCRIPTION_EXPIRE = 'subscription_expire'
SUBSCRIPTION_EXPIRE_DETAIL = 'Notifies a user that subscription has expired.'
ACTIVATION_EMAIL = 'activation_email'
ACTIVATION_EMAIL_DETAIL = 'Notifies self-registered users that they need to activate their account.'
