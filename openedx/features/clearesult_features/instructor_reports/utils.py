"""
Helper functions for Clearesult credits.
"""

import six

from logging import getLogger
from student.models import CourseEnrollment
from django.conf import settings
from django.contrib.auth.models import User
from django.test import RequestFactory
from xmodule.modulestore.django import modulestore

from lms.djangoapps.certificates.models import CertificateStatuses, GeneratedCertificate
from lms.djangoapps.grades.api import CourseGradeFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.clearesult_features.models import (
    ClearesultUserProfile,
    ClearesultCourseCredit,
    ClearesultCreditProvider,
    UserCreditsProfile,
    ClearesultCourseCompletion
)
from openedx.features.clearesult_features.models import ClearesultCourse, ClearesultGroupLinkage
from openedx.features.clearesult_features.utils import (
    get_course_progress, get_site_linked_courses_and_groups,
    get_group_users, get_site_users, add_timezone_to_datetime
)

logger = getLogger(__name__)


def get_available_credits_provider_list(course_key):
    available_providers_list = []
    used_course_providers_short_codes = [
        used_credit.credit_type.short_code for used_credit in ClearesultCourseCredit.objects.filter(
                course_id=course_key
            )
    ]
    available_providers = ClearesultCreditProvider.objects.exclude(short_code__in=used_course_providers_short_codes)
    for provider in available_providers:
        available_providers_list.append({
            'name': provider.name,
            'short_code': provider.short_code,
        })
    return available_providers_list


def get_course_credits_list(course_key):
    course_credits = []
    for course_credit in ClearesultCourseCredit.objects.filter(course_id=course_key):
        course_credits.append({
            'credit_type_code': course_credit.credit_type.short_code,
            'credit_type_name': course_credit.credit_type.name,
            'credits': course_credit.credit_value
        })
    return course_credits


def get_all_credits_provider_list():
    all_providers_list = []
    for provider in ClearesultCreditProvider.objects.all():
        all_providers_list.append({
            'name': provider.name,
            'short_code': provider.short_code,
        })
    return all_providers_list


def generate_user_course_credits(course_id, user):
    course_credits = ClearesultCourseCredit.objects.filter(course_id=course_id)
    user_credits = UserCreditsProfile.objects.filter(user=user).prefetch_related('earned_course_credits')

    for user_credit in user_credits:
        for course_credit in course_credits:
            if (user_credit.credit_type == course_credit.credit_type and
                    course_credit not in user_credit.earned_course_credits.all()):
                user_credit.earned_course_credits.add(course_credit)
                logger.info(
                    'Add Credits => {} credits from credit_provider: {} have been added for user: {} and course {}.'.format(
                        course_credit.credit_value, course_credit.credit_type.name, user.email, course_id
                    )
                )


def remove_user_course_credits_if_exist(course_id, user):
    course_credits = ClearesultCourseCredit.objects.filter(course_id=course_id)
    user_credits = UserCreditsProfile.objects.filter(user=user).prefetch_related('earned_course_credits')

    for user_credit in user_credits:
        for course_credit in course_credits:
            if (user_credit.credit_type == course_credit.credit_type and
                    course_credit in user_credit.earned_course_credits.all()):
                user_credit.earned_course_credits.remove(course_credit)
                logger.info(
                    'Remove Credits => {} credits from credit_provider: {} have been removed for user: {} and course {}.'.format(
                        course_credit.credit_value, course_credit.credit_type.name, user.email, course_id
                    )
                )


def get_user_course_earned_credits(course_id, user):
    result = set();
    user_credits = UserCreditsProfile.objects.filter(user=user)

    for user_credit in user_credits:
        result = result.union(set(user_credit.earned_course_credits.filter(course_id=course_id)))

    return [{'name': r.credit_type.name, 'code': r.credit_type.short_code, 'credits': r.credit_value} for r in result]


def get_credit_provider_by_short_code(short_code):
    try:
        return ClearesultCreditProvider.objects.get(short_code=short_code)
    except ClearesultCreditProvider.DoesNotExist:
        return None


def get_user_credits_profile_data_for_credits_report(allowed_sites, provider_filter=None, pass_date_filter=None):
    if allowed_sites:
        # allowed_sites can contain data in following situations:
        # when user wants to generate report only for request-site (default-behavior).
        # when local admin wants to get data for all of it's allowed sites

        # retrieve users only for allowed sites list.
        groups = ClearesultGroupLinkage.objects.filter(site__in=allowed_sites)
        site_users = get_group_users(groups)

    if provider_filter and allowed_sites:
        # Either global/local admins scenerio for request-site - with provider filter
        # OR local admin scenerio for all allowed sites - with provider filter
        user_credits_profiles = UserCreditsProfile.objects.filter(user__in=site_users, credit_type__short_code=provider_filter)
    elif provider_filter and not allowed_sites:
        # global admin scenerio for all sites - with provider filter
        user_credits_profiles = UserCreditsProfile.objects.filter(credit_type__short_code=provider_filter)
    elif not provider_filter and allowed_sites:
        # Either global/local admins scenerio for request-site - without provider filter
        # OR local admin scenerio for all allowed sites - without provider filter
        user_credits_profiles = UserCreditsProfile.objects.filter(user__in=site_users)
    else:
        # global admin scenerio for all sites - without provider filter
        user_credits_profiles = UserCreditsProfile.objects.all()

    return user_credits_profiles


def check_date_with_filter(date_filter, date):
    is_valid = True
    if not date or date == 'N/A':
        is_valid = False
    elif date_filter:
        if date_filter.get('start') and (date < date_filter.get('start')):
            is_valid = False

        if date_filter.get('end') and (date > date_filter.get('end')):
            is_valid = False
    return is_valid


def list_user_credits_for_report(course_key, allowed_sites, provider_filter=None, pass_date_filter=None):
    """
    Return info about user who have earned course credits after successfull completion of the courses.
    It will also apply filtration on the basis of given provider_filter.

    list_user_credits_for_report(course_key, provider_filter)
    would return [
        {
            'username': 'username1',
            'provider_id': 'user1_provider_id',
            'provider'': 'provider name'
            'email': 'email1,
            'course_id': 'course-v1:test1+course1+id1',
            'course_name': 'sample course name 1',
            'earned_credits': '1',
            'grade_percent': '0.83',
            'letter_grade': 'A',
            'pass_date': '2021-01-01'
        },
        {
            'username': 'username2',
            'provider_id': 'user2_provider_id',
            'provider'': 'provider name'
            'email': 'email2,
            'course_id': 'course-v1:test1+course1+id1',
            'course_name': 'sample course name 1',
            'earned_credits': '1',
            'grade_percent': '0.78',
            'letter_grade': 'PASS',
            'pass_date': '2021-01-03'
        },
        {
            'username': 'username1',
            'provider_id': 'user1_provider_id',
            'provider'': 'provider name'
            'email': 'email1,
            'course_id': 'course-v1:test1+course2+id2',
            'course_name': 'sample course name 2',
            'earned_credits': '3',
            'grade_percent': '0.80',
            'letter_grade': 'A',
            'pass_date': '2021-01-03'
        }
    ]

    ! Note that result will only contain results for the user earned credits.
    We will not include users who have completed the course but didn't get any credits.
    """
    data_list = []
    user_credits_profiles = get_user_credits_profile_data_for_credits_report(allowed_sites, provider_filter, pass_date_filter)

    for user_provider_profile in user_credits_profiles:
        user_credit_courses = user_provider_profile.earned_course_credits.all()
        if user_credit_courses.count() > 0:
            for course_credit in user_credit_courses:
                is_pass_date_valid_with_filter = True
                try:
                    pass_date = ClearesultCourseCompletion.objects.get(user=user_provider_profile.user,
                        course_id=course_credit.course_id).pass_date
                except ClearesultCourseCompletion.DoesNotExist:
                    pass_date = None

                if pass_date:
                    pass_datetime = pass_date
                    pass_date = pass_date.date()
                else:
                    pass_datetime = pass_date = 'N/A'

                if pass_date_filter.get('start') or pass_date_filter.get('end'):
                    is_pass_date_valid_with_filter = check_date_with_filter(pass_date_filter, pass_date)

                if is_pass_date_valid_with_filter:
                    course_grade = CourseGradeFactory().read(user_provider_profile.user, course_key=course_credit.course_id)
                    course = modulestore().get_course(course_credit.course_id)
                    data = {
                        'username': user_provider_profile.user.username,
                        'email': user_provider_profile.user.email,
                        'user_provider_id': user_provider_profile.credit_id,
                        'provider_name': user_provider_profile.credit_type.name,
                        'provider_short_code':  user_provider_profile.credit_type.short_code,
                        'course_id': course_credit.course_id,
                        'course_name': course.display_name,
                        'earned_credits': course_credit.credit_value,
                        'grade_percent': course_grade.percent,
                        'letter_grade': course_grade.letter_grade,
                        'pass_date': add_timezone_to_datetime(pass_datetime, settings.CLEARESULT_REPORTS_TZ)
                    }
                    data_list.append(data)
        else:
            if not(pass_date_filter.get('start') or pass_date_filter.get('end')):
                data = {
                    'username': user_provider_profile.user.username,
                    'email': user_provider_profile.user.email,
                    'user_provider_id': user_provider_profile.credit_id,
                    'provider_name': user_provider_profile.credit_type.name,
                    'provider_short_code':  user_provider_profile.credit_type.short_code,
                    'course_id': 'N/A',
                    'course_name': 'N/A',
                    'earned_credits': 0.0,
                    'grade_percent': 'N/A',
                    'letter_grade': 'N/A',
                    'pass_date': 'N/A'
                }
                data_list.append(data)

    return data_list


def list_user_total_credits_for_report(course_key, allowed_sites, provider_filter=None):
    """
    Return info about user accumulative earned credits. It will also apply filtration on the basis of
    given provider_filter.

    list_user_total_credits_for_report(course_key, provider_filter)
    would return [
        {
            'username': 'username1',
            'provider_id': 'user1_provider_id',
            'provider'': 'provider name'
            'email': 'email1,
            'total_earned_credits': '3',
        },
        {
            'username': 'username2',
            'provider_id': 'user2_provider_id',
            'provider'': 'provider name'
            'email': 'email2,
            'total_earned_credits': '1',
        },
        {
            'username': 'username1',
            'provider_id': 'user1_provider_id',
            'provider'': 'provider name'
            'email': 'email1,
            'total_earned_credits': 2
        }
    ]

    ! Note that result will only contain results for the users who have registered CUI for any Provider.
    """
    data_list = []
    user_credits_profiles = get_user_credits_profile_data_for_credits_report(allowed_sites, provider_filter=None)

    for user_provider_profile in user_credits_profiles:
        total_earned_credits = 0
        for course_credit in user_provider_profile.earned_course_credits.all():
            total_earned_credits += course_credit.credit_value

        data = {
            'username': user_provider_profile.user.username,
            'email': user_provider_profile.user.email,
            'user_provider_id': user_provider_profile.credit_id,
            'provider_name': user_provider_profile.credit_type.name,
            'provider_short_code':  user_provider_profile.credit_type.short_code,
            'total_earned_credits': total_earned_credits
        }
        data_list.append(data)

    return data_list


def list_all_course_enrolled_users_progress_for_report(allowed_sites, course_id, is_course_level=False):
    """
    Return info about user all courses enrolled students progress details.
    would return [
        {
            'user_id': '1',
            'email': 'username1@example.com',
            'username'': 'username1'
            'first_name': 'Jhon',
            'last_name': 'Doe',
            'course_id': 'course-v1:edX+def17+def17',
            'course_name': 'Example course name',
            'enrollment_status'': 'enrolled'
            'enrollment_mode': 'honor',
            'enrollment_date': '2020-10-11',
            'progress_percent': '100%',
            'grade_percent': '20%',
            'letter_grade': 'Pass"
            'completion_date': '2020-10-22',
            'pass_date'': '2020-10-22'
            'certificate_eligible': 'Y',
            'certificate_delivered': 'N',
        }
    ]

    ! Note that result will only contain results for the users who have registered CUI for any Provider.
    """
    data = []
    all_active_enrollments = []

    if allowed_sites == None and not is_course_level:
        # user is superuser and report is for all courses of all sites
        all_active_enrollments = CourseEnrollment.objects.filter(is_active=True)
    elif allowed_sites == None and is_course_level:
        # user is superuser and report is for only for current course
        all_active_enrollments = CourseEnrollment.objects.filter(is_active=True, course_id=course_id)
    else:
        # user is local admin

        # retrieve site courses
        site_courses, groups = get_site_linked_courses_and_groups(allowed_sites)

        # retrieve site users
        site_users = get_group_users(groups)

        if not is_course_level:
            # user is local-admin and report is for all courses of allowed sites
            site_courses_ids = [course.course_id for course in site_courses]
            all_active_enrollments = CourseEnrollment.objects.filter(is_active=True, course_id__in=site_courses_ids, user__in=site_users)
        else:
            # user is local-admin and report is only for current course
            all_active_enrollments = CourseEnrollment.objects.filter(is_active=True, course_id=course_id, user__in=site_users)

    for enrollment in all_active_enrollments:
        user = enrollment.user
        request = RequestFactory().get(u'/')
        request.user = user
        course_id = enrollment.course_id

        progress = get_course_progress(request, enrollment.course)
        try:
            completion_obj = ClearesultCourseCompletion.objects.get(user=user, course_id=course_id)
            pass_date = completion_obj.pass_date
            completion_date = completion_obj.completion_date
        except ClearesultCourseCompletion.DoesNotExist:
            completion_date = pass_date = None

        try:
            certificate = GeneratedCertificate.eligible_certificates.get(user=user, course_id=course_id)
            certificate_status = certificate.status
        except GeneratedCertificate.DoesNotExist:
            certificate = certificate_status = None

        course_grade = CourseGradeFactory().read(user, course_key=course_id)

        user_course_dict = {
            'user_id': user.id,
            'email': user.email,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'course_id': six.text_type(enrollment.course.id),
            'course_name': enrollment.course.display_name,
            'enrollment_status': "enrolled" if CourseEnrollment.is_enrolled(user, course_id) else "unenrolled",
            'enrollment_mode': enrollment.mode,
            'enrollment_date':  add_timezone_to_datetime(enrollment.created, settings.CLEARESULT_REPORTS_TZ),
            'progress_percent': "{} %".format(progress),
            'grade_percent': "{} %".format(course_grade.percent * 100),
            'letter_grade': course_grade.letter_grade,
            'completion_date': add_timezone_to_datetime(completion_date, settings.CLEARESULT_REPORTS_TZ) if (completion_date and progress == 100) else 'N/A',
            'pass_date': add_timezone_to_datetime(pass_date, settings.CLEARESULT_REPORTS_TZ) if pass_date else 'N/A',
            'certificate_eligible': 'Y' if course_grade.passed else 'N',
            'certificate_delivered': 'Y' if certificate_status == CertificateStatuses.downloadable else 'N'
        }

        data.append(user_course_dict)

    return data


def list_all_site_wise_registered_users_for_report(site, is_site_level, date_joined_filter=None):
    """
    Return info of the users' registration

    Data would be in this format
    [
        {
            'user_id': 1,
            'username': 'John',
            'email': 'john@example.com',
            'first_name': 'John',
            'last_name': 'Wick',
            'date_joined': '2020-12-07'
        },
        ...
        ...
    ]
    """
    data = []
    users = []
    if is_site_level:
        users = get_site_users(site)
    else:
        users = User.objects.filter(is_active=True)

    for user in users:
        if user.is_active:
            sites_associated = ''
            try:
                sites_associated = user.clearesult_profile.site_identifiers
            except ClearesultUserProfile.DoesNotExist:
                pass

            actual_joined_date = user.date_joined.date()
            if date_joined_filter.get('start') or date_joined_filter.get('end'):
                if check_date_with_filter(date_joined_filter, actual_joined_date):
                    user_info = {
                        'user_id': user.id,
                        'username': user.username,
                        'email': user.email,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'date_joined': add_timezone_to_datetime(user.date_joined, settings.CLEARESULT_REPORTS_TZ),
                        'sites_associated': sites_associated
                    }
                    data.append(user_info)
            else:
                user_info = {
                    'user_id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'date_joined': add_timezone_to_datetime(user.date_joined, settings.CLEARESULT_REPORTS_TZ),
                    'sites_associated': sites_associated
                }
                data.append(user_info)

    return data
