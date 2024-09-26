"""
Populate cohorts for a course if cohorts tab is broken.
"""

import logging

from django.core.management import BaseCommand, CommandError

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.courseware.courses import get_course
from openedx.core.djangoapps.course_groups.models import CourseCohort, CourseUserGroup


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Populate cohorts for a course.
    """
    help = 'Populate cohorts for a course'

    def add_arguments(self, parser):
        """
        Add arguments to the command parser.
        """
        parser.add_argument(
            '--course',
            action='store',
            type=str,
            required=True,
            help='The course ID of the course whose cohorts need to be populated.'
        )

    def handle(self, *args, **options):
        course_id = options['course']
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            raise CommandError('Course ID %s is incorrect' % course_id)

        course = get_course(course_key)
        cohorts = CourseUserGroup.objects.filter(
            course_id=course.id, group_type=CourseUserGroup.COHORT).exclude(name__in=course.auto_cohort_groups)
        logger.info('Number of cohorts: %s', cohorts.count())
        logger.info('Cohorts: %s', ', '.join(cohorts.values_list('name', flat=True)))
        for cohort in cohorts:
            CourseCohort.create(course_user_group=cohort)

        logger.info('Cohorts populated successfully')
