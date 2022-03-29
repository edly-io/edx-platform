"""
Django admin command to update user credits on the basis of earned certificates.
"""

from logging import getLogger

from django.core.management.base import BaseCommand

from lms.djangoapps.grades.api import CourseGradeFactory
from openedx.features.clearesult_features.models import (
    ClearesultCourseCredit,
    UserCreditsProfile
)

logger = getLogger(__name__)


class Command(BaseCommand):
    """
    This command attempts to update users earned credits.

    On reading course grade, CourseGradeFactory will send signals of COURSE_GRADE_NOW_PASSED and
    COURSE_GRADE_NOW_FAILED and our custom listeners in signals.py will automatically take action
    and update course credits for the user.

    Example usage:
        $ ./manage.py lms update_user_earned_credits
    """
    help = 'Command to update user earned credits.'

    def handle(self, *args, **options):
        logger.info('=> Generating Course credits for the users.')
        for course_credit in ClearesultCourseCredit.objects.all():
            for user_credit in UserCreditsProfile.objects.all():
                if (user_credit.credit_type.short_code == course_credit.credit_type.short_code):
                    course_grade = CourseGradeFactory().read(user_credit.user, course_key=course_credit.course_id)
