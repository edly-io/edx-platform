from openedx.core.djangoapps.ace_common.message import BaseMessageType


class MandatoryCoursesEnrollmentNotification(BaseMessageType):
    """
    A message for notifying students about mandatory courses enrollment.
    """
    pass


class MandatoryCoursesApproachingDueDatesNotification(BaseMessageType):
    """
    A message for notifying Students about mandatory courses approching due date.
    """
    pass

class MandatoryCoursesPassedDueDatesNotification(BaseMessageType):
    """
    A message for notifying Admins about mandatory courses passed due dates.
    """
    pass


class CoursePassedNotification(BaseMessageType):
    """
    A message for notifying learner that he has passed a course.
    """
    pass


class EventEnrollmentNotification(BaseMessageType):
    """
    A message for notifying user about event enrollment.
    """
    pass


class CourseEnrollmentNotification(BaseMessageType):
    """
    A message for notifying user about course enrollment.
    """
    pass


class CourseEndReminderNotification(BaseMessageType):
    """
    A message for notifying enrolled users about course end date.
    """
    pass


class EventStartReminderNotification(BaseMessageType):
    """
    A message for notifying enrolled users about event start date.
    """
    pass
