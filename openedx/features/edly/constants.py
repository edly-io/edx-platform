"""
Constants for Edly feature app.
"""

# PLAN CHOICES
TRIAL = 'trial'
ESSENTIALS = 'essentials'
ELITE = 'elite'
EDLY_SAAS = 'edly_saas'
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
COURSE_ENROLLMENT = 'course_enrollment'
ROLE_ASSIGNED = 'role_assigned'
ROLE_REVOKED = 'role_revoked'
SUBSCRIPTION_EXPIRE = 'subscription_expire'
ACTIVATION_EMAIL = 'activation_email'

# DEFAULT COURSE IMAGE
DEFAULT_COURSE_IMAGE = "images_course_image.jpg"
DEFAULT_COURSE_IMAGE_PATH = 'images/images_course_image.jpg'
DEFAULT_QA_PROMPT = """Use the following pieces of context to answer the question at the end.
If you don't know the answer, just say that you don't know, don't try to make up an answer.
{context}
Question: {question}
Helpful Answer:"""

CHATLY_COOKIE_NAME = "chatly_token"
COURSE_SIZE_LIMIT = 50 * 1024 * 1024
CHATLY_IMPORT_ERROR_MESSAGES = {
    "The folder course/vertical is missing in uploaded zip": (
        "The course has either no data for syncing or changes are not published"
    ),
    "The folder course/sequential is missing in uploaded zip": (
        "The course has either no data for syncing or changes are not published"
    ),
    "The folder course/html is missing in uploaded zip": (
        "The course has either no data for syncing or changes are not published"
    ),
}
