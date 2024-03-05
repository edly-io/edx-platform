"""
celery tasks for the course progress emails
"""
import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from edx_ace import ace
from edx_ace.recipient import Recipient
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.grades.api import CourseGradeFactory
from openedx.core.djangoapps.ace_common.message import BaseMessageType
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.content.block_structure.api import get_block_structure_manager
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.sdaia_features.course_progress.models import CourseCompletionEmailHistory
from xmodule.modulestore.django import modulestore
from openedx.features.course_experience.url_helpers import get_learning_mfe_home_url

logger = logging.getLogger(__name__)


class UserCourseProgressEmail(BaseMessageType):
    """
    Message Type Class for User Course Progress Email
    """
    APP_LABEL = 'course_progress'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options['transactional'] = True


class UserCourseCompletionEmail(BaseMessageType):
    """
    Message Type Class for User Course Completion Email
    """
    APP_LABEL = 'course_progress'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options['transactional'] = True


@shared_task
def send_user_course_progress_email(current_progress, progress_last_email_sent_at, course_completion_percentages_for_email, course_key, user_id):
    """
    Sends User Activation Code Via Email
    """
    user = User.objects.get(id=user_id)
    course_id = CourseKey.from_string(course_key)
    course = modulestore().get_course(course_id)

    site_domain = configuration_helpers.get_value_for_org(
        course.org,
        'site_domain',
        settings.LMS_HOST
    )
    site = Site.objects.get(domain=site_domain)
    message_context = get_base_template_context(site)
    course_home_url = get_learning_mfe_home_url(course_key=course_key, url_fragment='home')
    platform_name = configuration_helpers.get_value_for_org(
        course.org,
        'PLATFORM_NAME',
        settings.PLATFORM_NAME
    )

    context={
            'current_progress': int(current_progress),
            'progress_milestone_crossed': progress_last_email_sent_at,
            'course_key': course_key,
            'platform_name': platform_name,
            'course_name': course.display_name,
            'course_home_url': course_home_url,
        }
    message_context.update(context)
    user_language_pref = get_user_preference(user, LANGUAGE_KEY) or settings.LANGUAGE_CODE
    try:
        with emulate_http_request(site, user):
            msg = UserCourseProgressEmail(context=message_context).personalize(
                recipient=Recipient(0, user.email),
                language=user_language_pref,
                user_context={'full_name': user.profile.name}
            )
            ace.send(msg)
            logger.info('course progress email sent to user:')
            user_completion_progress_email_history = CourseCompletionEmailHistory.objects.get(user=user, course_key=course_key)
            user_completion_progress_email_history.last_progress_email_sent = course_completion_percentages_for_email
            user_completion_progress_email_history.save()
            return True
    except Exception as e:  # pylint: disable=broad-except
        logger.exception(str(e))
        logger.exception('Could not send course progress email sent to user')
        return False


@shared_task
def send_user_course_completion_email(user_id, course_key):
    course_id = CourseKey.from_string(course_key)
    user = User.objects.get(id=user_id)
    collected_block_structure = get_block_structure_manager(course_id).get_collected()
    course_grade = CourseGradeFactory().read(user, collected_block_structure=collected_block_structure)
    passing_grade = int(course_grade.percent * 100)

    course = modulestore().get_course(course_id)
    site_domain = configuration_helpers.get_value_for_org(
        course.org,
        'site_domain',
        settings.LMS_HOST
    )
    site = Site.objects.get(domain=site_domain)
    message_context = get_base_template_context(site)
    course_progress_url = get_learning_mfe_home_url(course_key=course_key, url_fragment='progress')
    platform_name = configuration_helpers.get_value_for_org(
        course.org,
        'PLATFORM_NAME',
        settings.PLATFORM_NAME
    )

    context={
            'course_key': course_key,
            'platform_name': platform_name,
            'course_name': course.display_name,
            'course_progress_url': course_progress_url,
            'passing_grade': passing_grade,
        }
    message_context.update(context)
    user_language_pref = get_user_preference(user, LANGUAGE_KEY) or settings.LANGUAGE_CODE
    try:
        with emulate_http_request(site, user):
            msg = UserCourseCompletionEmail(context=message_context).personalize(
                recipient=Recipient(0, user.email),
                language=user_language_pref,
                user_context={'full_name': user.profile.name}
            )
            ace.send(msg)
            logger.info('course completion email sent to user:')
            return True
    except Exception as e:  # pylint: disable=broad-except
        logger.exception(str(e))
        logger.exception('Could not send course completion email sent to user')
        return False
