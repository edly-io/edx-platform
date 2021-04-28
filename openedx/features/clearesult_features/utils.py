"""
Helper functions for clearesult_features app.
"""
import io
import json
import logging
import six
import copy
from csv import Error, DictReader, Sniffer
from datetime import datetime, timedelta

from edx_ace import ace
from edx_ace.recipient import Recipient
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.db.models import Sum, Case, When, IntegerField
from django.db.models.functions import Coalesce
from django.test import RequestFactory
from django.db.models import Q
from django.urls import reverse
from opaque_keys.edx.keys import CourseKey
from student.models import CourseEnrollment

from lms.djangoapps.instructor.enrollment import (
    get_email_params
)
from lms.djangoapps.courseware.courses import get_course_by_id
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.theming.helpers import get_current_site
from openedx.core.lib.celery.task_utils import emulate_http_request
from openedx.features.clearesult_features.message_types import (
    MandatoryCoursesNotification,
    MandatoryCoursesApproachingDueDatesNotification,
    MandatoryCoursesPassedDueDatesNotification
)
from openedx.features.clearesult_features.models import (
    ClearesultUserProfile, ClearesultCourse,
    ClearesultGroupLinkage, ClearesultGroupLinkedCatalogs,
    ClearesultLocalAdmin, ClearesultCourseCompletion,
    ClearesultCourseEnrollment, ClearesultCourseConfig
)
from openedx.features.course_experience.utils import get_course_outline_block_tree
from openedx.features.clearesult_features.tasks import check_and_enroll_group_users_to_mandatory_courses
from openedx.features.clearesult_features.api.v0.validators import validate_sites_for_local_admin

logger = logging.getLogger(__name__)


def get_file_encoding(file_path):
    """
    Returns the file encoding format.
    Arguments:
        file_path (str): Path of the file whose encoding format will be returned
    Returns:
        encoding (str): encoding format e.g: utf-8, utf-16, returns None if doesn't find
                        any encoding format
    """
    try:
        file = io.open(file_path, 'r', encoding='utf-8')
        encoding = None
        try:
            _ = file.read()
            encoding = 'utf-8'
        except UnicodeDecodeError:
            file.close()
            file = io.open(file_path, 'r', encoding='utf-16')
            try:
                _ = file.read()
                encoding = 'utf-16'
            except UnicodeDecodeError:
                logger.exception('The file encoding format must be utf-8 or utf-16.')

        file.close()
        return encoding

    except IOError as error:
        logger.exception('({}) --- {}'.format(error.filename, error.strerror))
        return None


def get_csv_file_control(file_path):
    """
    Returns opened file and dict_reader object of the given file path.
    """
    csv_file = None
    dialect = None
    try:
        encoding = get_file_encoding(file_path)
        if not encoding:
            logger.exception('Because of invlid file encoding format, user creation process is aborted.')
            return

        csv_file = io.open(file_path, 'r', encoding=encoding)
        try:
            dialect = Sniffer().sniff(csv_file.readline())
        except Error:
            logger.exception('Could not determine delimiter in the file.')
            csv_file.close()
            return

        csv_file.seek(0)
    except IOError as error:
        logger.exception('({}) --- {}'.format(error.filename, error.strerror))
        return

    dict_reader = DictReader(csv_file, delimiter=dialect.delimiter if dialect else ',')
    csv_reader = (dict((k.strip(), v.strip() if v else v) for k, v in row.items()) for row in dict_reader)

    return {'csv_file': csv_file, 'csv_reader': csv_reader}


def get_enrollments_and_completions(request, enrollments):
    """
    Returns user enrollment list for completed courses and incomplete courses
    and course completion dates as well.
    """
    complete_enrollments = []
    incomplete_enrollments = [enrollment for enrollment in enrollments]
    course_completions = {}
    for enrollment in enrollments:
        course_id_string = six.text_type(enrollment.course.id)
        course_outline_blocks = get_course_outline_block_tree(
            request, course_id_string, request.user
        )
        if course_outline_blocks:
            if course_outline_blocks.get('complete'):
                incomplete_enrollments.remove(enrollment)
                completion_date, pass_date = get_course_completion_and_pass_date(
                    enrollment.user, enrollment.course_id, is_graded=course_outline_blocks.get('graded')
                )
                course_completions[enrollment.id] = {
                        'completion_date': completion_date.date() if completion_date else None,
                        'pass_date': pass_date.date() if pass_date else None,
                }
                complete_enrollments.append(enrollment)

    return complete_enrollments, incomplete_enrollments, course_completions


def get_course_block_progress(course_block, CORE_BLOCK_TYPES, FILTER_BLOCKS_IN_UNIT):
    """
    Recursive helper function to walk course tree outline,
    returning total core blocks and total completed core blocks

    This function does not filter progress of blocks in
    FILTER_BLOCK_IN_UNIT list if they are alone in a single unit

    :param course_block: root block object or child block
    :param CORE_BLOCK_TYPES: list of core block types from the settings
    :param FILTER_BLOCKS_IN_UNIT: list of core block types to filter in a unit

    :return:
        total_blocks: count of blocks in a root block block or child block
        total_completed_blocks: count of completed core blocks in a root block or child block
    """
    if course_block is None:
        return 0, 0

    course_block_children = course_block.get('children')
    block_type = course_block.get('type')

    if not course_block_children:
        if block_type in CORE_BLOCK_TYPES:
            if course_block.get('complete'):
                return 1, 1
            else:
                return 1, 0

        return 0, 0

    total_blocks = 0
    total_completed_blocks = 0
    is_multi_block_type = len(set([block.get('type') for block in course_block_children])) > 1
    is_block_vertical = block_type == 'vertical'

    for block in course_block_children:
        if (is_block_vertical and block.get('type') in FILTER_BLOCKS_IN_UNIT and is_multi_block_type):
            continue

        total_count, completed_count = get_course_block_progress(
            block,
            CORE_BLOCK_TYPES,
            FILTER_BLOCKS_IN_UNIT
        )

        total_blocks += total_count
        total_completed_blocks += completed_count

    return total_blocks, total_completed_blocks


def get_site_users(site):
    """
    Returns users list belong to site.
    """
    site_users = []
    site_name = "-".join(site.name.split('-')[:-1]).rstrip()

    # ! Note: site name must contain "-" otherwise it will return emty string.
    if not site_name:
        logger.info("Site name ({}) is not in a correct format.".format(site.name))
        logger.info("Correct format is <site_name> - <site_type> i.e. 'blackhills - LMS'.")
        return site_users

    clearesult_user_profiles = ClearesultUserProfile.objects.exclude(extensions={}).select_related("user")

    for profile in clearesult_user_profiles:
        user_site_identifiers =  profile.extensions.get('site_identifier', [])

        if site_name in user_site_identifiers:
            site_users.append(profile.user)

    return  site_users


def create_clearesult_course(destination_course_key, source_course_key=None, site=None):
    """
    Create a clearesult course instance for a new course or course rerun.

    If you call this function for a course rerun,
        source_course_key: will be the course key of the parent course.
        destination_course_key: will be the course key of actual course rerun.
        site: will be None and we will use the same site for rerun
              which we have used for parent course.

    If you call this function for a course,
        source_course_key: will be None
        destination_course_key: will be the actual course key which has been created.
        site: will be the domain of site which is being linked to the clearesult course.
              If you get `Public` for this then it means the clearesult course will be `Public`
              and we will save `None` for that
    """
    if site == 'Public':
        site = None
    elif site == None and source_course_key:
        site = ClearesultCourse.objects.get(course_id=source_course_key).site
    else:
        site = Site.objects.get(domain=site)

    ClearesultCourse.objects.create(course_id=destination_course_key, site=site)


def get_site_for_clearesult_course(course_id):
    """
    Return site if you find any site linked to the course.
    Return 'Public' if course is public.
    Return None if there is no relation of course with site has been saved.

    If course is not linked to a site, it means course is public.
    If course is linked to a site, it means course it is private.
    """
    try:
        site = ClearesultCourse.objects.get(course_id=course_id).site
        if site is None:
            site = 'Public'
            return site

        return site.domain
    except ClearesultCourse.DoesNotExist:
        return None


def is_mandatory_course(enrollment):
    clearesult_groups = ClearesultGroupLinkage.objects.filter(users__username=enrollment.user.username)
    clearesult_catalogs = ClearesultGroupLinkedCatalogs.objects.filter(group__in=clearesult_groups)
    for clearesult_catalog in clearesult_catalogs:
        if clearesult_catalog.mandatory_courses.filter(course_id=enrollment.course_id).exists():
            return True
    return False

def get_calculated_due_date(request, enrollment):
    due_date = None
    try:
        config = get_mandatory_courses_due_date_config(request, enrollment)
        enrollment_date = enrollment.clearesultcourseenrollment.updated_date.date()
        due_date = enrollment_date + timedelta(days=int(config.get("mandatory_courses_alotted_time")))
    except Exception as ex:
        logger.error("Error has occured while calculating due_date of enrollment: {}, user: {}, course: {}".format(
            str(enrollment.id),
            enrollment.user.email,
            six.text_type(enrollment.course_id),
        ))
    return due_date


def get_incomplete_enrollments_clearesult_dashboard_data(request, enrollments):
    """
    Returns list of data that clearesult needs on student dahboard for incomeplete/in-progress courses section
    """
    data = []

    for enrollment in enrollments:
        data.append({
            'progress': get_course_progress(request, enrollment.course),
            'is_mandatory': is_mandatory_course(enrollment),
            'is_free': enrollment.mode in ['honor', 'audit'],
            'mandatory_course_due_date': get_calculated_due_date(request, enrollment)
        })

    return data


def get_course_progress(request, course):
    """
    Gets course progress percentages of the given course

    :param request: request object
    :param course_enrollments: enrolled course

    :return:
        courses_progress: progress percentage of each course in course_enrollments
    """
    CORE_BLOCK_TYPES = getattr(settings, 'CORE_BLOCK_TYPES', [])
    FILTER_BLOCKS_IN_UNIT = getattr(settings, 'FILTER_BLOCKS_IN_UNIT', [])

    course_id_string = six.text_type(course.id)
    course_outline_blocks = get_course_outline_block_tree(
        request, course_id_string, request.user
    )

    total_blocks, total_completed_blocks = get_course_block_progress(
        course_outline_blocks,
        CORE_BLOCK_TYPES,
        FILTER_BLOCKS_IN_UNIT
    )

    return round((total_completed_blocks / total_blocks) * 100) if total_blocks else 0


def is_local_admin_or_superuser(user):
    """
    If user is a local admin of any site or it's a superuser return True
    otherwise return False.
    """
    return user.is_superuser or ClearesultLocalAdmin.objects.filter(user=user).exists()


def get_course_completion_and_pass_date(user, course_id, is_graded):
    """
    Return course completion and pass date.

    The completion and pass date should be saved in ClearesultCourseCompletion
    according to the user enrollment. If it isn't, get the latest block completion
    date from BlockCompletion and save it.
    If course is not graded, completion date will be the pass date as well.

    ! Note: don't call this function if the course is not completed.
    """
    try:
        clearesult_course_completion = ClearesultCourseCompletion.objects.get(user=user, course_id=course_id)
    except ClearesultCourseCompletion.DoesNotExist:
        logger.info('Could not get completion for course {} and user {}'.format(course_id, user))
        return None, None

    return clearesult_course_completion.completion_date, clearesult_course_completion.pass_date


def generate_clearesult_course_completion(user, course_id):
    """
    On passing a course just set the pass date to the current date.
    """
    try:
        course_completion_object = ClearesultCourseCompletion.objects.get(
            user=user, course_id=course_id
        )
        if not course_completion_object.pass_date:
            course_completion_object.pass_date = datetime.now()
            course_completion_object.save()
    except ClearesultCourseCompletion.DoesNotExist:
        ClearesultCourseCompletion.objects.create (
            user=user, course_id=course_id, pass_date=datetime.now()
        )


def update_clearesult_course_completion(user, course_id):
    """
    This function will be called on course failure as it
    is associated with `COURSE_GRADE_NOW_FAILED` signal.

    So unless you pass a course for each step (attempting of problem)
    you will be considered as failed. Means this function will be called
    multiple times unlike `generate_clearesult_course_completion`

    In case of graded course and for failure just set the pass_date to None.
    For non graded course completion date will be the pass date.
    """
    is_graded = is_course_graded(course_id, user)
    clearesult_course_completion, created = ClearesultCourseCompletion.objects.get_or_create(
        user=user, course_id=course_id)

    if not created:
        if is_graded:
            clearesult_course_completion.pass_date = None
        else:
            clearesult_course_completion.pass_date = clearesult_course_completion.completion_date

        clearesult_course_completion.save()


def is_course_graded(course_id, user, request=None):
    """
    Check that course is graded.

    Arguments:
        course_id: (CourseKey/String) if CourseKey turn it into string
        request: (WSGI Request/None) if None create your own dummy request object

    Returns:
        is_graded (bool)
    """
    if request is None:
        request = RequestFactory().get(u'/')
        request.user = user

    if isinstance(course_id, CourseKey):
        course_id = six.text_type(course_id)

    course_outline = get_course_outline_block_tree(request, course_id, user)

    if course_outline:
        return course_outline.get('num_graded_problems') > 0
    else:
        return False


# TODO: Add newly registered users to their relevant site groups
def add_user_to_site_default_group(request, user, site):
    """
    Add user to default_group of a given site.
    ! request is important paramenter here should contain request.user and request.site
    """
    if site:
        logger.info("Add user: {} to site: {} default group.".format(user.email, site.domain))
        try:
            site_default_group = ClearesultGroupLinkage.objects.get(
                name=settings.SITE_DEFAULT_GROUP_NAME,
                site=site
            )
            if user not in site_default_group.users.all():
                site_default_group.users.add(user)
                check_and_enroll_group_users_to_mandatory_courses.delay(
                    site_default_group.id, [user.id], site_default_group.site.id, request.user.id)
        except ClearesultGroupLinkage.DoesNotExist:
            logger.error("Default group for site: {} doesn't exist".format(site.domain))


def is_lms_site(site):
    return "LMS" in site.name.upper()


def send_ace_message(request_user, request_site, dest_email, context, message_class):
    context.update({'site': request_site})

    with emulate_http_request(site=request_site, user=request_user):
        message = message_class().personalize(
            recipient=Recipient(username='', email_address=dest_email),
            language='en',
            user_context=context,
        )
        logger.info('Sending email notification with context %s', context)

        ace.send(message)


def send_notification(message_type, data, subject, dest_emails, request_user, current_site=None):
    """
    Send an email
    Arguments:
        message_type - string value to select ace message object
        data - Dict containing context/data for the template
        subject - Email subject
        dest_emails - List of destination emails
    Returns:
        a boolean variable indicating email response.
    """
    message_types = {
        'mandatory_courses': MandatoryCoursesNotification,
        'mandatory_courses_approaching_due_date': MandatoryCoursesApproachingDueDatesNotification,
        'mandatory_courses_passed_due_date': MandatoryCoursesPassedDueDatesNotification
    }

    if not current_site:
        current_site = get_current_site()

    data.update({'subject': subject})

    message_context = get_base_template_context(current_site)
    message_context.update(data)

    content = json.dumps(message_context)

    message_class = message_types[message_type]
    return_value = False

    base_root_url = current_site.configuration.get_value('LMS_ROOT_URL')
    logo_path = current_site.configuration.get_value(
        'LOGO',
        settings.DEFAULT_LOGO
    )

    platform_name = current_site.configuration.get_value('platform_name')
    message_context.update({
        "copyright_site_name": platform_name,
        "site_name":  current_site.configuration.get_value('SITE_NAME'),
        "logo_url": u'{base_url}{logo_path}'.format(base_url=base_root_url, logo_path=logo_path),
        "dashboard_url": "{}{}".format(base_root_url, message_context.get('dashboard_url'))
    })

    for email in dest_emails:
        message_context.update({
            "email": email
        })
        try:
            send_ace_message(request_user, current_site, email, message_context, message_class)
            logger.info(
                'Email has been sent to "%s" for content %s.',
                email,
                content
            )
            return_value = True
        except Exception as e:
            logger.error(
                'Unable to send an email to %s for content "%s".',
                email,
                content,
            )
            logger.error(e)

    return return_value


def send_mandatory_courses_emails(dest_emails, courses, request_user, request_site):
    email_params = {}
    subject = "Mandatory Courses Enrollment"

    logger.info("send mandatory course email to users: {}".format(dest_emails))

    key = "mandatory_courses"
    courses_data = [get_course_by_id(CourseKey.from_string(course_id)).display_name_with_default for course_id in courses]

    data = {
        "courses": courses_data
    }
    send_notification(key, data, subject, dest_emails, request_user, request_site)


def set_user_first_and_last_name(user, full_name):
    name_len = len(full_name)
    firstname = "N/A"
    lastname = "N/A"

    if name_len > 1:
        firstname = full_name[0]
        lastname = full_name[1]
    elif name_len > 0:
        firstname = full_name[0]

    if not user.first_name or user.first_name == 'N/A':
        user.first_name = firstname

    if not user.last_name or user.last_name == 'N/A':
        user.last_name = lastname

    user.save()


def get_site_from_site_identifier(user, site_identifier):
    lms_site_pattern = "{site_identifier} - LMS"
    try:
        return Site.objects.get(name=lms_site_pattern.format(site_identifier=site_identifier))
    except Site.DoesNotExist:
        logger.info("user with email: {} contains site identifier {} for which LMS site does not exist.".format(
            user.email, site_identifier
        ))
        return None


def prepare_magento_updated_customer_data(user, drupal_user_info, magento_customer, region):
    updated_magento_customer = magento_customer.copy()

    if updated_magento_customer.get('firstname') != user.first_name:
        updated_magento_customer['firstname'] = user.first_name
    if updated_magento_customer.get('lastname') != user.last_name:
        updated_magento_customer['lastname'] = user.last_name

    if not updated_magento_customer.get("addresses", []) and drupal_user_info and region:
        # Add new magento address
        drupal_user_address = drupal_user_info.get("address", {})
        updated_magento_customer["addresses"] = [
            {
                "firstname": user.first_name,
                "lastname": user.last_name,
                "company": drupal_user_info.get("company_name"),
                "street": [
                    drupal_user_address.get("street")
                ],
                "city": drupal_user_address.get("city"),
                "postcode": drupal_user_address.get("zip"),
                "country_id": drupal_user_info.get("country_code"),
                "region_id": region[0],
                "telephone": drupal_user_info.get("phone_number"),
                "default_billing": True,
                "default_shipping": True
            }
        ]
    else:
        # Check and update last name info in all magento existing addresses
        updated_address = copy.deepcopy(updated_magento_customer.get("addresses", []))
        for address in updated_address:
            if address.get('lastname') == 'N/A':
                address['lastname'] = user.last_name

        updated_magento_customer["addresses"] = updated_address

    return updated_magento_customer


def get_user_all_courses(user):
    all_courses = ClearesultCourse.objects.none()
    groups = ClearesultGroupLinkage.objects.filter(users__username=user.username)
    for courses in get_groups_courses_generator(groups):
        all_courses |= courses
    return all_courses.distinct()


def get_groups_courses_generator(groups):
    group_linked_catalogs = ClearesultGroupLinkedCatalogs.objects.filter(group__in=groups).prefetch_related('catalog')
    for group_linked_catalog in group_linked_catalogs:
        courses = group_linked_catalog.catalog.clearesult_courses.all()
        yield courses


def check_user_eligibility_for_clearesult_enrollment(user, course_id):
    """
    Check that the group of the specified user is linked with the catalog
    which contains the specified course or not.
    """
    groups = ClearesultGroupLinkage.objects.filter(users__username=user.username)
    for courses in get_groups_courses_generator(groups):
        if courses.filter(course_id=course_id).exists():
            return True
    return False


def filter_out_course_library_courses(courses, user):
    courses_list = []
    show_archive_courses = settings.FEATURES.get('SHOW_ARCHIVED_COURSES_IN_LISTING')

    if user.is_superuser or user.is_staff:
        # for superuser just check if for archive courses
        # superuser can see courses in course library.
        if not show_archive_courses:
            return [course for course in courses if not course.has_ended()]
        else:
            return courses

    error, allowed_sites = validate_sites_for_local_admin(user)
    if allowed_sites:
        # local admin flow
        # local admin will have access to all the linked courses
        accessble_courses = ClearesultCourse.objects.filter(Q(site__in=allowed_sites) | Q(site=None))
    else:
        # normal user flow
        accessble_courses = get_user_all_courses(user)

    user_courses = [course.course_id for course in accessble_courses]
    for course in courses:
        if course.id in user_courses:
            if show_archive_courses or (not show_archive_courses and not course.has_ended()):
                courses_list.append(course)

    return courses_list


def get_site_linked_courses_and_groups(sites):
    """
    It will return list of all courses that are somehow linked with given sites list user groups
    through public or private catalogs linkage.
    """
    all_courses = ClearesultCourse.objects.none()
    groups = ClearesultGroupLinkage.objects.filter(site__in=sites)
    for courses in get_groups_courses_generator(groups):
        all_courses |= courses

    return all_courses.distinct(), groups


def get_group_users(groups):
    """
    It will return all users of given user-groups.
    """
    site_users = User.objects.none()
    for group in groups:
        site_users |= group.users.all()

    return site_users.distinct()


def filter_courses_for_index_page_per_site(request, courses):
    """
    Filter to get only those courses whose catalogs are somehow
    associated with the user groups of the site.
    """
    clearesult_courses, _ = get_site_linked_courses_and_groups([request.site])

    clearesult_courses_ids = []

    for clearesult_course in clearesult_courses:
        clearesult_courses_ids.append(clearesult_course.course_id)

    filtered_courses = []
    for course in courses:
        if course.id in clearesult_courses_ids:
            filtered_courses.append(course)

    return filtered_courses


def update_clearesult_enrollment_date(enrollment):  # pylint: disable=unused-argument
    if enrollment.is_active:
        try:
            logger.info("Update enrollment date as enrolled status is changed for user: {} and course: {}.".format(
                enrollment.user.email,
                six.text_type(enrollment.course_id)
            ))

            enrollment.clearesultcourseenrollment.updated_date=datetime.now()
            enrollment.clearesultcourseenrollment.save()
        except CourseEnrollment.clearesultcourseenrollment.RelatedObjectDoesNotExist:
            ClearesultCourseEnrollment.objects.create(
                enrollment=enrollment,
                updated_date=datetime.now(),
            )
        logger.info("Enrollment date has been updated user: {} and course: {}.".format(
            enrollment.user.email,
            six.text_type(enrollment.course_id)
        ))


def get_site_prefered_mandatory_courses_due_dates_config(site, course_id):
    """
    Mandatory Courses due dates can be managed as follows
    - site default configs in ClearesultSiteConfigurations
    - course specific configs in ClearesultCourseConfig

    Priority has been given to course specific configs but if course specific configs is not there for the mandatory
    course then site defaults will be used.

    Returns config dict as follows:
    {
        "mandatory_courses_alotted_time": 10,
        "mandatory_courses_notification_period": 2,
        "site": <Site obj>
    }
    """
    config = {}
    try:
        course_config = ClearesultCourseConfig.objects.get(site=site, course_id=course_id)
        config = {
            "mandatory_courses_alotted_time": course_config.mandatory_courses_alotted_time,
            "mandatory_courses_notification_period": course_config.mandatory_courses_notification_period
        }

    except ClearesultCourseConfig.DoesNotExist:
        site_config = site.clearesult_configuration.latest('change_date')
        config = {
            "mandatory_courses_alotted_time": site_config.mandatory_courses_alotted_time,
            "mandatory_courses_notification_period": site_config.mandatory_courses_notification_period
        }

    config.update({"site": site})
    return config


def get_shortest_config(sites_list, course_id):
    """
    Find mandatory courses config with shortest alotted time.

    let's say, Site-A has alotted time 10 days and Site-B has 20 days for course-abc which is mandatory for both
    sites then Site-A config should be user as 10 < 20.
    """
    total_sites = len(sites_list)

    if total_sites:
        if total_sites == 1:
            return get_site_prefered_mandatory_courses_due_dates_config(sites_list[0], course_id)
        else:
            # find shortest:
            config = {}
            for site in sites_list:
                site_config = get_site_prefered_mandatory_courses_due_dates_config(site, course_id)
                if site_config.get("mandatory_courses_alotted_time", 999999) < config.get("mandatory_courses_alotted_time", 999999):
                    config = site_config
            return config

    else:
        return {}


def get_mandatory_courses_due_date_config(request, enrollment):
    """
    Find mandatory courses config.

    if course is private -> use course-site config
    if course is public -> use config of the site  with shortest aloted time.
    """
    config = {}
    try:
        clearesult_course = ClearesultCourse.objects.get(course_id=enrollment.course_id)
    except ClearesultCourse.DoesNExceptionotExist:
        logger.error("Clearesult course does not exist for course_id {}".format(six.text_type(course_id)))
        return config

    if clearesult_course.site!=None:
        # course is private
        config = get_site_prefered_mandatory_courses_due_dates_config(clearesult_course.site, enrollment.course_id)
    else:
        # course is public
        linkages = ClearesultGroupLinkedCatalogs.objects.filter(
            mandatory_courses__course_id=enrollment.course_id,
            group__users__id=enrollment.user.id
        )

        # extract all sites on which user is linked with the course
        linked_sites = []
        if len(linkages):
            for linkage in linkages:
                if linkage.group.site not in linked_sites:
                    linked_sites.append(linkage.group.site)

            config = get_shortest_config(linked_sites, enrollment.course_id)

    return config


def send_course_due_date_approching_email(request, config, enrollment):
    """
    Send email to student about approaching due dates that X days are remaining in due date.
    """
    site = config.get("site")
    key = "mandatory_courses_approaching_due_date"
    subject = "Mandatory Courses Approaching Due Date "

    logger.info("Send mandatory course approching due date email to user: {}".format(enrollment.user.email))

    course = get_course_by_id(enrollment.course_id)
    root_url = site.configuration.get_value("LMS_ROOT_URL").strip("/")
    course_url = "{}{}".format(root_url, reverse('course_root', kwargs={'course_id': enrollment.course_id}))

    email_params = {
        "days_left": config.get("mandatory_courses_notification_period"),
        "full_name": enrollment.user.first_name + " " + enrollment.user.last_name,
        "display_name": course.display_name_with_default,
        "course_url": course_url
    }
    return send_notification(key, email_params, subject, [enrollment.user.email], request.user, site)


def send_due_date_passed_email_to_admins(passed_due_dates_site_users):
    """
    Send email to admins about the student hasn't completed course with in aloted time.
    """
    email_key = "mandatory_courses_passed_due_date"
    subject = "Mandatory Courses Due Date Passed"
    request_user = User.objects.filter(is_active=True, is_superuser=True).first()

    for key, value in passed_due_dates_site_users.items():
        try:
            dest_emails = settings.SUPPORT_DEST_EMAILS
            site = Site.objects.get(domain=key)
            site_local_admins = ClearesultLocalAdmin.objects.filter(site=site)
            dest_emails.extend([localAdmin.user.email for localAdmin in site_local_admins])
            logger.info("Send mandatory course passed due date email to admins: {} of site: {}".format(dest_emails, key))
            email_params = {
                "site_enrollments": value
            }
            send_notification(email_key, email_params, subject, dest_emails, request_user, site)
        except Site.DoesNotExist:
            logger.info("Couldn't send mandatory course passed due date email as Site for domain:{} doesn't exist.".format(key))


def is_public_course(course_key):
    if ClearesultCourse.objects.filter(course_id=course_key, site=None).exists():
        return True
    return False
