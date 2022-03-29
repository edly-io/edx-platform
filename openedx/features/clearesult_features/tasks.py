import json
import logging
import requests
import six

from celery import task
from celery_utils.logged_task import LoggedTask
from datetime import datetime
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from student.models import CourseEnrollment
from django.test import RequestFactory
from django.urls import reverse

from lms.djangoapps.courseware.courses import get_course_by_id
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.clearesult_features.models import (
    ClearesultUserProfile, ClearesultCourse,
    ClearesultGroupLinkage, ClearesultGroupLinkedCatalogs,
    ClearesultLocalAdmin, ClearesultCourseCompletion
)
from opaque_keys.edx.keys import CourseKey
from lms.djangoapps.instructor.enrollment import enroll_email
from openedx.features.clearesult_features.magento.client import MagentoClient
from openedx.features.clearesult_features.drupal.client import DrupalClient, InvalidDrupalCredentials
from openedx.features.clearesult_features.emails.task_utils import (
    get_site_courses, get_about_to_expire_trainings,
    get_eligible_enrolled_users_for_reminder_emails
)

log = logging.getLogger('edx.celery.task')


def _log_reminder_emails_report(expire_trainings, error_trainings, courses_emails_count, events_emails_count, events_error_emails, courses_error_emails):
    log.info('\n\n\n')
    log.info("--------------------- REMINDER EMAILS STATS - {} ---------------------".format(
        datetime.now().date().strftime("%m-%d-%Y")
    ))

    log.info('Total number of about to expire trainings: {}'.format(len(expire_trainings)))
    log.info('Trainings encountered processing error: {}'.format(len(error_trainings)))

    log.info('Total number users who should get reminder email of courses: {}'.format(courses_emails_count))
    log.info('Total number users who should get reminder email of events: {}'.format(events_emails_count))

    log.info('Error encountered for event reminder emails: {}'.format(len(events_error_emails)))
    log.info('Error encountered for courses reminder emails: {}'.format(len(courses_error_emails)))

    log.info('Unable to send reminder emails of events: {}'.format(events_error_emails))
    log.info('Unable to send reminder emails of courses: {}'.format(courses_error_emails))


@task(base=LoggedTask)
def check_and_send_reminder_emails():
    from openedx.features.clearesult_features.utils import (
        get_site_users,
        send_course_end_reminder_email,
        send_event_start_reminder_email
    )
    request = RequestFactory().get(u'/')
    sites = Site.objects.filter(name__icontains="LMS")

    # super user that will be used to send emails
    request.user = User.objects.get(username=settings.ADMIN_USERNAME_FOR_EMAIL_TASK)

    #some variables for starts
    email_count_for_courses = email_count_for_events = 0
    emails_error_for_courses = []
    emails_error_for_events = []
    processing_error_on_trainings = []
    about_to_expire_trainings = []

    for site in sites:
        request.site = site
        site_config = site.clearesult_configuration.latest('change_date')
        site_groups = ClearesultGroupLinkage.objects.filter(site=site).prefetch_related(
            'clearesultgrouplinkedcatalogs_set__catalog',
            'clearesultgrouplinkedcatalogs_set__mandatory_courses'
        )
        site_courses = get_site_courses(site_groups)
        site_users = get_site_users(site)
        site_expire_trainings = get_about_to_expire_trainings(site_config, site_courses)
        about_to_expire_trainings.extend(site_expire_trainings)
        for training in site_expire_trainings:
            try:
                enrollments = CourseEnrollment.objects.filter(
                    is_active=True,
                    course_id=training.course_id,
                    user__in=site_users
                )
                if training.is_event:
                    # for event - send reminder email to all registered users
                    users = [enrollment.user for enrollment in enrollments]
                    email_count_for_events += len(users)
                    send_event_start_reminder_email(users, training, request, site_config.events_notification_period, emails_error_for_events)

                else:
                    # enrollments are only eligible for the emails if course is not mandatory for that user
                    # and enrolled user has not completed course.
                    users = get_eligible_enrolled_users_for_reminder_emails(request, training, enrollments, site_groups)
                    email_count_for_courses += len(users)
                    send_course_end_reminder_email(
                        users, training, request, site_config.courses_notification_period, emails_error_for_courses)
            except Exception as ex:
                log.error(ex)
                processing_error_on_trainings.append(six.text_type(training.course_id))

    _log_reminder_emails_report(
        about_to_expire_trainings, processing_error_on_trainings, email_count_for_courses,
        email_count_for_events, emails_error_for_events, emails_error_for_courses
    )


@task(base=LoggedTask)
def enroll_students_to_mandatory_courses(user_ids, course_ids, request_site_id, request_user_id=None):
    """
    enroll given users to the given clearesult_courses list if not already enrolled
    """
    from lms.djangoapps.instructor.views.api import students_update_enrollment
    from openedx.features.clearesult_features.utils import (
        send_mandatory_courses_enrollment_email, update_clearesult_enrollment_date
    )

    log.info("TASK: enroll student to mandatory courses has been called.")

    try:
        request_site = Site.objects.get(id=request_site_id)

        if request_user_id:
            request_user = User.objects.get(id=request_user_id)
        else:
            # any super user
            request_user = User.objects.get(username=settings.ADMIN_USERNAME_FOR_EMAIL_TASK)

    except (User.DoesNotExist, Site.DoesNotExist):
        log.info("TASK Error: email task can not be called without request_user and request_site.")
        return

    users = []
    email_course_data = {}

    #convert user_ids list to users object list
    for user_id in user_ids:
        try:
            user_obj = User.objects.get(id=user_id)
            users.append(user_obj)
        except:
            log.info("TASK Error: user with id: {} does not request_site_id".format(user_id))

    for course_id in course_ids:
        # filter out students who are already enrolled in the course
        not_enrolled_users = [user for user in users if not CourseEnrollment.is_enrolled(user, course_id)]
        course_key = CourseKey.from_string(course_id)

        for user in not_enrolled_users:
            log.info("TASK: enroll user with email: {} in course_id: {}".format(user.email, course_id))
            before, after, enrollment_obj = enroll_email(course_key, user.email, True, False, {})

            before_status = before.to_dict()
            after_status = after.to_dict()

            # if before enrollment status was false and after status is true means user is now enrolled
            if (not before_status.get('enrollment', False)) and after_status.get('enrollment', False):
                update_clearesult_enrollment_date(enrollment_obj)

                if email_course_data.get(user.email):
                    new_value = email_course_data.get(user.email)
                    #avoid duplicate mandatory courses
                    if course_id not in new_value:
                        new_value.append(course_id)
                else:
                    new_value = [course_id]

                email_course_data.update({user.email: new_value})
            else:
                log.info(
                    "TASK Error: enrollment status didn't change for user: {}".format(user.email)
                )

    for key, value in email_course_data.items():
        if value:
            log.info("user: {} has been seccussfully enrolled in following courses: {}".format(key, value))
            send_mandatory_courses_enrollment_email([key], value, request_user, request_site)


@task(base=LoggedTask)
def check_and_enroll_group_users_to_mandatory_courses(group_id, newly_added_group_user_ids, req_site_id, req_user_id=None):

    log.info(
        "TASK: check and enroll groups users to mandatory courses has been called for group_id: {}".format(group_id))

    group_catalogs = ClearesultGroupLinkedCatalogs.objects.filter(group__id=group_id)

    all_madatory_courses = []

    # extract all mandatory courses fromm all linked catalogs of the group
    for group_catalog in group_catalogs:
        mandatory_courses = group_catalog.mandatory_courses.all()
        if mandatory_courses:
            # calling task from task - use apply_async
            all_madatory_courses.extend([six.text_type(course.course_id) for course in mandatory_courses])

    if len(all_madatory_courses):
        enroll_students_to_mandatory_courses(
            newly_added_group_user_ids, all_madatory_courses, req_site_id, req_user_id)


@task(base=LoggedTask)
def call_drupal_logout_endpoint(email):
    try:
        client = DrupalClient()
        is_success = client.logout_user(email)
    except InvalidDrupalCredentials:
        log.info("Drupal credentials error has been orccured.")
        is_success = False

    if is_success:
        log.info('Success: User with email {} has been successfully logged out from Drupal.'.format(email))
    else:
        log.info('Failed: User with email {} has not been logged out from Drupal.'.format(email))


@task(base=LoggedTask)
def update_magento_user_info_from_drupal(email, magento_base_url, magento_token):
    from openedx.features.clearesult_features.utils import (
        set_user_first_and_last_name, prepare_magento_updated_customer_data
    )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        log.error("Task Error - update_magento_user_info - User with email {} does not exist".format(email))
        return

    try:
        magento_client = MagentoClient(user, magento_base_url, magento_token)
        magento_customer = magento_client.get_customer_data()
    except:
        # no need to return here - as we still need to check and update edx user's first name last name from drupal latest
        magento_customer = None

    try:
        drupal_client = DrupalClient()
        user_info = drupal_client.get_user_data(email)
    except InvalidDrupalCredentials:
        # no need to return here - as we still need to check and update magento user first name and last name
        user_info = None

    if user_info:
        set_user_first_and_last_name(
            user,
            [user_info.get('first_name', 'N/A'), user_info.get('last_name', 'N/A')]
        )
    else:
        log.error("Task Error - Unable to fetch user's data from Drupal.")

    if magento_customer:
        country_code = user_info.get("country_code")
        region_code = user_info.get("address", {}).get("state")
        region = None
        if country_code and region_code:
            region = magento_client.get_region_details(country_code, region_code)
        else:
            log.error("Missing Drupal Country code or region code.")

        updated_magento_customer = prepare_magento_updated_customer_data(user, user_info, magento_customer, region)

        if updated_magento_customer != magento_customer:
            success = magento_client.update_customer_with_address(updated_magento_customer)
            if success:
                log.info("User with email {} has been successfully updated on Magento with latest user info.".format(email))
            else:
                log.info("Unable to update user with email {} on Magento with latest user info".format(email))
        else:
            log.info("User with email {} is already updated with latest user info on Magento".format(email))

            if not region:
                log.info("Unable to update address due to missing region - firstname and lastname is already updated".format(email))
    else:
        log.error("Task Error - Unable to fetch magento customer from Magento.")


@task(base=LoggedTask)
def send_course_pass_email_to_learner(user_id, course_key_string, is_course_graded):
    user = User.objects.get(id=user_id)
    associated_sites = user.clearesult_profile.get_associated_sites()
    if not associated_sites:
        log.exception("No associated sites are available for {}.".format(user.email))
        return

    course_id = CourseKey.from_string(course_key_string)
    site = associated_sites[0]
    with emulate_http_request(site=site, user=user):
        key = "course_passed"
        subject = "Course Passed"

        log.info("Send course passed email to user: {}".format(user.email))

        course = get_course_by_id(course_id)
        root_url = site.configuration.get_value("LMS_ROOT_URL").strip("/")
        course_progress_url = "{}{}".format(root_url, reverse('progress', kwargs={'course_id': course_id}))

        email_params = {
            "full_name": user.first_name + " " + user.last_name,
            "display_name": course.display_name_with_default,
            "course_progress_url": course_progress_url,
            "is_course_graded": is_course_graded
        }
        from openedx.features.clearesult_features.utils import send_notification
        return send_notification(key, email_params, subject, [user.email], user, site)
