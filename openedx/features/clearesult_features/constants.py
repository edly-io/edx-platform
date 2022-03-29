from openedx.features.clearesult_features import message_types

# CLEARESULT CACHE CONSTANTS

# it will be used with user's username
# e.g: ali_allowed_site_names
ALLOWED_SITES_NAMES_CACHE_KEY_SUFFIX = '_allowed_sites_names'
ALLOWED_SITES_NAMES_CACHE_TIMEOUT = 864000  # 864000 seconds = 10 days is the cache timeout
AFFILIATION_INFO_TIMEOUT = 240 # 240 seconds = 4 minutes is the cache timeout

# it will be used with user's email
# e.g: ali@example.com_clearesult_session
USER_SESSION_CACHE_KEY_SUFFIX = '_clearesult_sessions'
USER_SESSION_CACHE_TIMEOUT = 864000  # 864000 seconds = 10 days is the cache timeout

# CLEARESULT MESSAGE TYPES FOR EMAILS
MESSAGE_TYPES = {
    'mandatory_courses_enrollment': message_types.MandatoryCoursesEnrollmentNotification,
    'mandatory_courses_approaching_due_date': message_types.MandatoryCoursesApproachingDueDatesNotification,
    'mandatory_courses_passed_due_date': message_types.MandatoryCoursesPassedDueDatesNotification,
    'course_passed': message_types.CoursePassedNotification,
    'event_enrollment': message_types.EventEnrollmentNotification,
    'course_enrollment': message_types.CourseEnrollmentNotification,
    'course_end_reminder': message_types.CourseEndReminderNotification,
    'event_start_reminder': message_types.EventStartReminderNotification
}
