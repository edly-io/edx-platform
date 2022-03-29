"""
Django admin command to migrate the
user's course related activity.
"""
import json
import six
from completion.models import BlockCompletion
from csv import Error
from logging import getLogger
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from lms.djangoapps.courseware.courses import get_course_by_id
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.model_data import set_score
from lms.djangoapps.grades.course_data import CourseData
from openedx.features.clearesult_features.models import UserCreditsProfile, ClearesultCourseCredit
from openedx.features.clearesult_features.utils import get_csv_file_control
from student.models import CourseEnrollment

logger = getLogger(__name__)


class Command(BaseCommand):
    """
    This command migrates the course enrollment, grades and credits.
    Example usage:
        $ ./manage.py lms import_course_enrollments '/tmp/course_enrollments_file.csv'

    We will be using a CSV file which contains the values for following fields
    Email, Course, Course ID

    The pre-requisite for this command is that you must have run the
    `import_user_accounts` command before this command
    """
    help = ('Migrate user activity for the users whose accounts have'
            ' been migrated with the "import_user_accounts" command')

    def add_arguments(self, parser):
        parser.add_argument(
            'file_path',
            type=str,
            help='The absolute path of csv file which contains the user course activity details.'
        )

    def handle(self, *args, **options):
        file_path = options['file_path']
        file_controller = get_csv_file_control(file_path)
        if not file_controller:
            logger.info('Unable to get file control.')
            return

        try:
            for row in file_controller['csv_reader']:

                user = _get_user_by_email(row.get('Email').lower())
                if not user:
                    continue

                course_key = _get_course_key(row.get('Course ID'), row.get('Course'))
                if not course_key:
                    continue

                _enroll_user(user, course_key)
                course = get_course_by_id(course_key, depth=None)
                block_locators = _get_block_locators(course)
                _assign_grades_to_scorm_blocks(user, course, block_locators)
                providers_for_course = ClearesultCourseCredit.objects.filter(course_id=row.get('Course ID'))
                _assign_credits_to_users(user, providers_for_course, row.get('Course ID'))

        except Error as err:
            logger.exception('Error while traversing {} file content with following error {}.'
                             .format(file_path, err))

        file_controller['csv_file'].close()


def _get_user_by_email(email):
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        logger.exception('The user account with email address {} does not exist.'.format(email))

    return user


def _get_course_key(course_key_string, course_name):
    try:
        course_key = CourseKey.from_string(course_key_string)
    except InvalidKeyError:
        logger.exception('The course with this id and name does not exist.'.format(course_key_string, course_name))

    return course_key


def _enroll_user(user, course_key):
    try:
        CourseEnrollment.objects.get(user=user, course=course_key, is_active=True)
    except CourseEnrollment.DoesNotExist:
        CourseEnrollment.enroll(user, course_key)


def _assign_grades_to_scorm_blocks(user, course, block_locators):
    for block_locator in block_locators:
        block = modulestore().get_item(block_locator)
        if 'type@scormxblock+block@' in six.text_type(block_locator) and block.graded:
            set_score(user.id, block_locator, 1, 1)
            block_state = {
                'lesson_score': 1,
                'lesson_status': 'completed',
                'cmi.exit': 'suspend',
                'data_scorm': {
                    'cmi.exit': 'suspend',
                    'cmi.session_time': 'PT0.35S',
                    'cmi.suspend_data': '',
                    'cmi.location': 0,
                    'cmi.score.max': '100',
                    'cmi.score.min': '0',
                    'cmi.score.scaled': 1,
                    'cmi.completion_status': 'completed',
                    'cmi.success_status': 'passed',
                },
                'completion_status': 'completed',
                'success_status': 'passed',
                'is_migrated_by_script': True
            }

            # # updating scorm xblock state
            student_module = StudentModule.objects.get(student=user, course_id=course.id,
                                                       module_state_key=block_locator)
            student_module.state = json.dumps(block_state)
            student_module.save()
        BlockCompletion.objects.submit_completion(
            user=user,
            block_key=block_locator,
            completion=1.0,
        )


def _assign_credits_to_users(user, providers_for_course, course_id_string):
    for provider in providers_for_course:
        user_credit_record = UserCreditsProfile.objects.filter(user=user, credit_type=provider.credit_type)
        if user_credit_record:
            if not UserCreditsProfile.objects.filter(user=user, credit_type=provider.credit_type,
                                                     earned_course_credits=provider):
                user_credit_record[0].earned_course_credits.add(provider)


def _get_block_locators(course):
    course_block_locators = []
    course_data = CourseData(user=None, course=course)
    for id in course_data.collected_structure.get_block_keys():
        course_block_locators.append(id)

    return course_block_locators
