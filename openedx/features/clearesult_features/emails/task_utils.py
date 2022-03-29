import logging
import six
from pytz import utc
from completion.models import BlockCompletion
from datetime import datetime, timedelta
from django.contrib.auth.models import User

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.course_experience.utils import get_course_outline_block_tree
from openedx.features.clearesult_features.models import ClearesultCourse

logger = logging.getLogger('edx.celery.task')


def is_course_completed(request, enrollment):
    course_outline_blocks = get_course_outline_block_tree(
        request, six.text_type(enrollment.course_id), enrollment.user
    )

    if course_outline_blocks:
        return course_outline_blocks.get('complete')
    else:
        return True


def get_site_courses(site_groups):
    site_courses = ClearesultCourse.objects.none()
    for group in site_groups:
        linkages = group.clearesultgrouplinkedcatalogs_set.all()
        for linkage in linkages:
            courses = linkage.catalog.clearesult_courses.all()
            site_courses |= courses

    return site_courses


def get_about_to_expire_trainings(site_config, site_courses):
    about_to_expire_trainings = []
    for course in site_courses.distinct():
        try:
            course_overview = CourseOverview.get_from_id(course.course_id)
        except CourseOverview.DoesNotExist:
            logger.error("Unable to find Course Overview object for id: ".format(course_id))
            continue;

        if course.is_event and course_overview.start_date and not course_overview.has_started():
            if (course_overview.start_date - timedelta(days=site_config.events_notification_period)).date() == datetime.now(utc).date():
                about_to_expire_trainings.append(course)
        elif course_overview.end_date and not course_overview.has_ended():
            if (course_overview.end_date - timedelta(days=site_config.courses_notification_period)).date() == datetime.now(utc).date():
                about_to_expire_trainings.append(course)

    return about_to_expire_trainings


def get_eligible_enrolled_users_for_reminder_emails(request, clearesult_course, enrollments, site_groups):
    filtered_enrolled_users_emails = []
    for enrollment in enrollments:
        if not is_course_completed(request, enrollment):
            is_mandatory = site_groups.filter(
                users__email=enrollment.user.email,
                clearesultgrouplinkedcatalogs__mandatory_courses__course_id=clearesult_course.course_id
            ).exists()
            if not is_mandatory:
                filtered_enrolled_users_emails.append(enrollment.user)

    return filtered_enrolled_users_emails
