"""
Properly assign course cohorts to learners.
"""

import logging

from django.core.management import BaseCommand, CommandError

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.course_groups.cohorts import get_course_cohorts, add_user_to_cohort


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Populate learners in cohorts for a course.
    """
    help = 'Populate learners in cohorts for a course'

    def add_arguments(self, parser):
        """
        Add arguments to the command parser.
        """
        parser.add_argument(
            '--course',
            action='store',
            type=str,
            required=True,
            help='Comma separated course IDs of the courses whose learners need to be linked to cohorts.'
        )

    def handle(self, *args, **options):
        course_ids = options['course'].split(',')
        for course_id in course_ids:
            try:
                course_key = CourseKey.from_string(course_id)
            except InvalidKeyError:
                raise CommandError('Course ID %s is incorrect' % course_id)

            course_cohorts = get_course_cohorts(course_id=course_key)
            logger.info('%s cohorts found for %s', len(course_cohorts), course_id)
            for cohort in course_cohorts:
                logger.info('%s users found for cohort %s', cohort.users.all().count(), cohort.name)
                for user in cohort.users.all():
                    logger.info('Adding %s to cohort %s', '{} ({})'.format(user.username, user.email), cohort.name)
                    try:
                        cohort_user, previous_cohort, __ = add_user_to_cohort(cohort, user)
                        if previous_cohort:
                            logger.info('%s previously belonged to %s', user.email, previous_cohort)
                    except ValueError:
                        logger.warning('User %s already present in the cohort %s', user.email, cohort.name)
