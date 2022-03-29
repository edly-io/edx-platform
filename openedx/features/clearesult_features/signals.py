"""
Signals for clearesult features django app.
"""
import six
from logging import getLogger

from completion.models import BlockCompletion
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.dispatch import receiver
from django.db.models import Q
from django.db.models.signals import post_save, pre_delete, pre_save

from course_modes.models import CourseMode
from lms.djangoapps.verify_student.models import ManualVerification
from student import auth
from student.models import UserProfile
from student.roles import CourseStaffRole, CourseInstructorRole
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from openedx.core.djangoapps.signals.signals import COURSE_GRADE_NOW_PASSED, COURSE_GRADE_NOW_FAILED
from openedx.features.clearesult_features.models import (
    ClearesultCourseCompletion, ClearesultGroupLinkage,
    ClearesultSiteConfiguration, ClearesultLocalAdmin,
    ClearesultCourse
)
from openedx.features.clearesult_features.instructor_reports.utils import (
    generate_user_course_credits,
    remove_user_course_credits_if_exist,
)
from openedx.features.clearesult_features.utils import (
    generate_clearesult_course_completion,
    update_clearesult_course_completion,
    is_course_graded, is_lms_site,
)
from openedx.features.clearesult_features.tasks import send_course_pass_email_to_learner

logger = getLogger(__name__)


def _add_users_as_instructor_to_course(course_id, users):
    role = CourseStaffRole(course_id)
    for user in users:
        auth.add_users(User.objects.get(username=settings.ADMIN_USERNAME_FOR_EMAIL_TASK), role, user)


def _remove_users_instructor_access_from_course(course_id, users):
    role = CourseStaffRole(course_id)
    for user in users:
        if role.has_user(user, check_user_activation=False):
            auth.remove_users(User.objects.get(username=settings.ADMIN_USERNAME_FOR_EMAIL_TASK), role, user)


@receiver(post_save, sender=CourseOverview)
def create_default_course_mode(sender, instance, created, **kwargs):
    if not (settings.FEATURES.get('ENABLE_DEFAULT_COURSE_MODE_CREATION') and created):
        logger.info('Flag is not set - Skip Auto creation of default course mode.')
        return

    default_mode_slug = settings.COURSE_MODE_DEFAULTS['slug']
    if default_mode_slug != "audit":
        logger.info('Generating Default Course mode: {}'.format(default_mode_slug))
        course_mode = CourseMode(
            course=instance,
            mode_slug=default_mode_slug,
            mode_display_name=settings.COURSE_MODE_DEFAULTS['name'],
            min_price=settings.COURSE_MODE_DEFAULTS['min_price'],
            currency=settings.COURSE_MODE_DEFAULTS['currency'],
            expiration_date=settings.COURSE_MODE_DEFAULTS['expiration_datetime'],
            description=settings.COURSE_MODE_DEFAULTS['description'],
            sku=settings.COURSE_MODE_DEFAULTS['sku'],
            bulk_sku=settings.COURSE_MODE_DEFAULTS['bulk_sku'],
        )
        course_mode.save()
    else:
        logger.info('No need to generate Course mode for Audit mode.')


@receiver(post_save, sender=UserProfile)
def generate_manual_verification_for_user(sender, instance, created, **kwargs):
    """
    Generate ManualVerification for the User (whose UserProfile instance has been created).
    """
    if not (settings.FEATURES.get('ENABLE_AUTOMATIC_ACCOUNT_VERIFICATION') and created):
        return

    logger.info('Generating ManualVerification for user: {}'.format(instance.user.email))
    try:
        ManualVerification.objects.create(
            user=instance.user,
            status='approved',
            reason='SKIP_IDENTITY_VERIFICATION',
            name=instance.name
        )
    except Exception:  # pylint: disable=broad-except
        logger.error('Error while generating ManualVerification for user: %s', instance.user.email, exc_info=True)


@receiver(COURSE_GRADE_NOW_PASSED)
def genrate_user_course_credits_and_course_completion(sender, user, course_id, **kwargs):  # pylint: disable=unused-argument
    """
    Listen for a learner passing a course and update user credits and completion dates.
    """
    generate_user_course_credits(course_id, user)
    generate_clearesult_course_completion(user, course_id)


@receiver(COURSE_GRADE_NOW_FAILED)
def remove_user_course_credits_and_update_course_completion(sender, user, course_id, **kwargs):  # pylint: disable=unused-argument
    """
    Listen for a learner failing a course and update user credits and completion dates.
    """
    remove_user_course_credits_if_exist(course_id, user)
    update_clearesult_course_completion(user, course_id)


@receiver(post_save, sender=BlockCompletion)
def set_clearesult_course_completion(sender, instance, created, **kwargs):
    """
    Listen for block completion and update clearesult course completion.

    On each save of BlockCompletion save on ClearesultCourseCompletion as well.
    For graded courses don't mess up with the pass date. But for non graded courses
    pass date will be same as of completion date.
    """
    if created:
        if is_course_graded(instance.context_key, instance.user):
            defaults = {
                'completion_date':instance.created,
            }
        else:
            defaults = {
                'completion_date':instance.created,
                'pass_date': instance.created
            }

        ClearesultCourseCompletion.objects.update_or_create(
            user=instance.user, course_id=instance.context_key,
            defaults=defaults
        )

@receiver(post_save, sender=SiteConfiguration)
def create_default_group(sender, instance, created, **kwargs):
    """
    Listen for SiteConfiguration and create default site group on new site creation.
    """
    if is_lms_site(instance.site):
        clearesult_configuration = ClearesultSiteConfiguration.current(instance.site)

        if clearesult_configuration:
            # check if default group is set for the site
            try:
                if not clearesult_configuration.default_group:
                    raise ClearesultGroupLinkage.DoesNotExist
            except ClearesultGroupLinkage.DoesNotExist:
                # if default group is not set then get or create DEFAULT group for the site and set it
                # as default group
                default_site_group = ClearesultGroupLinkage.objects.get_or_create(
                    name=settings.SITE_DEFAULT_GROUP_NAME,
                    site=instance.site
                )
                clearesult_configuration.default_group = default_site_group[0]
                clearesult_configuration.save()
        else:
            default_site_group = ClearesultGroupLinkage.objects.get_or_create(
                    name=settings.SITE_DEFAULT_GROUP_NAME,
                    site=instance.site
            )
            clearesult_configuration.objects.create(
                site=instance.site, default_group=default_site_group[0], security_code_required=False, enabled=True
            )


@receiver(post_save, sender=ClearesultLocalAdmin)
def check_and_give_staff_access_to_related_courses(sender, instance, created, **kwargs):
    """
    Newly added local admin should have instructor access to all local and public course.
    """
    if created:
        site_courses = ClearesultCourse.objects.filter(Q(site=instance.site) | Q(site=None))
        for course in site_courses:
            _add_users_as_instructor_to_course(course.course_id, [instance.user])


@receiver(pre_delete, sender=ClearesultLocalAdmin)
def check_and_revert_staff_access_from_related_courses(sender, instance, **kwargs):
    """
    When local admin access is deleted - we need to revert instructor access from all local and public courses
    """
    site_courses = ClearesultCourse.objects.filter(Q(site=instance.site) | Q(site=None))
    for course in site_courses:
        _remove_users_instructor_access_from_course(course.course_id, [instance.user] )


@receiver(post_save, sender=ClearesultCourse)
def check_and_add_existing_local_admins_to_the_course(sender, instance, created, **kwargs):
    """
    Check and give existing local-admins an instructor access to newly created local course
    """
    if created:
        if instance.site:
            # for local course - retrieve all local admins of the site
            existing_local_admins = ClearesultLocalAdmin.objects.filter(site=instance.site)
        else:
            # for public course - retrieve all existing local admins of all sites
            existing_local_admins = ClearesultLocalAdmin.objects.all()

        users = [admin.user for admin in existing_local_admins]
        _add_users_as_instructor_to_course(instance.course_id, users)


@receiver(pre_delete, sender=ClearesultCourse)
def check_and_remove_existing_local_admins_from_the_courses(sender, instance, **kwargs):
    """
    Check and revert instructor access of existing local-admins from deleted local course
    """
    if instance.site:
        # for local course - retrieve all local admins of the site
        existing_local_admins = ClearesultLocalAdmin.objects.filter(site=instance.site)
    else:
        # for public course - retrieve all existing local admins of all sites
        existing_local_admins = ClearesultLocalAdmin.objects.all()

    users = [admin.user for admin in existing_local_admins]
    # remove instructor role of existing local admins from the course-team
    _remove_users_instructor_access_from_course(instance.course_id, users)


@receiver(pre_save, sender=ClearesultCourseCompletion)
def send_email_to_learner_on_passing_course(sender, instance, **kwargs):
    course_key_string = six.text_type(instance.course_id)
    pass_email_sent = cache.get('pass_email_{}_{}'.format(course_key_string, instance.user.id))
    if not pass_email_sent:
        cache.set('pass_email_{}_{}'.format(course_key_string, instance.user.id), True, 60)
        try:
            old_instance = ClearesultCourseCompletion.objects.get(id=instance.id)
            if instance.pass_date and instance.pass_date != old_instance.pass_date:
                is_graded = is_course_graded(course_key_string, instance.user)
                send_course_pass_email_to_learner.delay(instance.user.id, course_key_string, is_graded)
        except ClearesultCourseCompletion.DoesNotExist:
            if instance.pass_date:
                is_graded = is_course_graded(course_key_string, instance.user)
                send_course_pass_email_to_learner.delay(instance.user.id, course_key_string, is_graded)
