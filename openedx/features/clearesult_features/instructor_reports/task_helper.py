import json
from datetime import datetime
from pytz import UTC
from time import time
from util.file import course_filename_prefix_generator

from logging import getLogger
from django.contrib.auth.models import User
from django.contrib.sites.models import Site

from lms.djangoapps.instructor_analytics.csvs import format_dictlist
from lms.djangoapps.instructor_task.api_helper import submit_task
from lms.djangoapps.instructor_task.models import ReportStore
from lms.djangoapps.instructor_task.tasks_helper.runner import TaskProgress
from lms.djangoapps.instructor_task.tasks_helper.utils import tracker_emit
from openedx.features.clearesult_features.instructor_reports.tasks import (
    calculate_credits_csv,
    calculate_all_courses_progress_csv,
    get_site_registered_users_csv
)
from openedx.features.clearesult_features.instructor_reports.utils import (
    list_user_credits_for_report,
    list_user_total_credits_for_report,
    list_all_course_enrolled_users_progress_for_report,
    list_all_site_wise_registered_users_for_report
)
from openedx.features.clearesult_features.api.v0.validators import validate_sites_for_local_admin

logger = getLogger(__name__)


def get_csv_name(sites, csv_name, is_site_level):
    """
    It will format csv-name in a following way:

    - when superuser generates report, append "global" in the start of given csv-name.
    - when local-admin generates a report, append his allowed site names in the start of given csv-name

    """
    csv_name_format = "{}{}"
    if not is_site_level:
        return csv_name_format.format("global__", csv_name)
    else:
        site_string = ""
        for site in sites:
            site_name = "-".join(site.name.split('-')[:-1]).rstrip()
            site_string += "{}_".format(site_name)
        site_string += "_"
        return csv_name_format.format(site_string, csv_name)


def submit_calculate_all_courses_progress_csv(request, course_key, features, task_type):
    """
    Submits a task to generate a CSV file containing information about
    users enrollment and progress status in all courses.

    Raises AlreadyRunningError if said file is already being updated.
    """
    task_class = calculate_all_courses_progress_csv

    # json loads will help to convert string bool value i.e 'false' to Python bool value i.e False
    task_input = {
        'features': features,
        'request_user_id': request.user.id,
        'is_course_level': json.loads(request.POST.get('is_course_level', 'false').lower()),
        'is_site_level': json.loads(request.POST.get('is_site_level', 'true').lower()),
        'request_site_domain': request.site.domain,

    }
    return submit_task(request, task_type, task_class, course_key, task_input, '')


def submit_calculate_credits_csv(request, course_key, features, task_type):
    """
    Submits a task to generate a CSV file containing information about
    earned user credits details.

    Raises AlreadyRunningError if said file is already being updated.
    """
    task_class = calculate_credits_csv
    task_input = {
        'features': features,
        'provider_filter': request.POST.get('provider_filter', ''),
        'pass_date_filter_start': request.POST.get('pass_date_filter_start', ''),
        'pass_date_filter_end': request.POST.get('pass_date_filter_end', ''),
        'csv_type': task_type,
        'request_user_id': request.user.id,
        'is_site_level': json.loads(request.POST.get('is_site_level', 'true').lower()),
        'request_site_domain': request.site.domain,
    }
    task_key = ''

    return submit_task(request, task_type, task_class, course_key, task_input, task_key)


def submit_get_registered_users_csv(request, course_key, features, task_type):
    """
    Submits a task to generate a CSV file containing information about
    registered users and their date of joining.

    Raises AlreadyRunningError if said file is already being updated.
    """
    task_class = get_site_registered_users_csv
    task_input = {
        'features': features,
        'date_joined_filter_start': request.POST.get('date_joined_filter_start', ''),
        'date_joined_filter_end': request.POST.get('date_joined_filter_end', ''),
        'csv_type': task_type,
        'request_user_id': request.user.id,
        'request_site_domain': request.site.domain,
        'is_site_level': json.loads(request.POST.get('is_site_level', 'true').lower()),
    }
    task_key = ''

    return submit_task(request, task_type, task_class, course_key, task_input, task_key)


def upload_credits_csv(_xmodule_instance_args, _entry_id, course_id, task_input, action_name):
    """
    Generate a CSV file containing information about students who have earned course credits after successful
    completion of the course which means after getting passing grades in the courses.
    """
    csv_name = "credits_info"
    student_data = []
    start_time = time()
    start_date = datetime.now(UTC)
    num_reports = 1
    task_progress = TaskProgress(action_name, num_reports, start_time)
    current_step = {'step': 'Calculating credits'}
    task_progress.update_task_state(extra_meta=current_step)
    error = None

    # Compute result table and format it
    query_features = task_input.get('features')
    provider_filter = task_input.get('provider_filter')
    pass_date_start = task_input.get('pass_date_filter_start')
    pass_date_end = task_input.get('pass_date_filter_end')

    pass_date_filter = {
        'start': datetime.strptime(pass_date_start, "%Y-%m-%d").date() if pass_date_start else None,
        'end': datetime.strptime(pass_date_end, "%Y-%m-%d").date() if pass_date_end else None
    }

    csv_type = task_input.get('csv_type')
    request_user_id = task_input.get('request_user_id')
    request_site_domain = task_input.get('request_site_domain')
    is_site_level = task_input.get('is_site_level')

    if not is_site_level:
        try:
            request_user = User.objects.get(id=request_user_id)
        except User.DoesNotExist:
            logger.error("Unable to generate grand courses report - User does not exist.")

        error, allowed_sites = validate_sites_for_local_admin(request_user)
    else:
        try:
            request_site = Site.objects.get(domain=request_site_domain)
            allowed_sites = [request_site]
        except Site.DoesNotExist:
            error = "Unable to generate grand courses report - Site with domain: {} does not exist.".format(request_site_domain)

    if error:
        # user is normal user - not authorized to view reports
        # OR requested site does not exist
        logger.error(error)
    else:
        if csv_type == 'credits':
            query_features_names = [
                'Username', 'Email', 'User Provider ID (CUI)', 'Provider Name', 'Provider Code',
                'Course ID', 'Course Name', 'Earned Credits', 'Grade %', 'Grade', 'Pass Date'
            ]

            student_data = list_user_credits_for_report(
                course_id, allowed_sites, provider_filter, pass_date_filter)
            csv_name = 'user_earned_credits_info'
        else:
            query_features_names = ['Username', 'Email', 'User Provider ID (CUI)', 'Provider Name', 'Total Earned Credits']
            student_data = list_user_total_credits_for_report(course_id, allowed_sites, provider_filter)
            csv_name = 'user_accumulative_credits_info'

    if provider_filter:
        csv_name = u'{csv_name}_filter_{provider}.csv'.format(csv_name=csv_name, provider=provider_filter)

    csv_name = get_csv_name(allowed_sites, csv_name, is_site_level)
    header, rows = format_dictlist(student_data, query_features)

    task_progress.attempted = task_progress.succeeded = len(rows)
    task_progress.skipped = task_progress.total - task_progress.attempted

    rows.insert(0, query_features_names)

    current_step = {'step': 'Uploading CSV'}
    task_progress.update_task_state(extra_meta=current_step)

    # Perform the upload
    upload_credits_csv_to_report_store(rows, csv_name, course_id, start_date, False)

    return task_progress.update_task_state(extra_meta=current_step)


def upload_credits_csv_to_report_store(rows, csv_name, course_id, timestamp,
                                       course_level_report=True, config_name='GRADES_DOWNLOAD'):
    """
    Upload credits data as a CSV using ReportStore. It will not append course name in the csv file name for the
    site level reports like overall earned credits reports of students.

    Arguments:
        rows: CSV data in the following format (first column may be a
            header):
            [
                [row1_colum1, row1_colum2, ...],
                ...
            ]
        csv_name: Name of the resulting CSV
        course_id: ID of the course
    """
    report_store = ReportStore.from_config(config_name)
    if course_level_report:
        report_name = u'{course_prefix}_{csv_name}_{timestamp_str}.csv'.format(
            course_prefix=course_filename_prefix_generator(course_id),
            csv_name=csv_name,
            timestamp_str=timestamp.strftime('%Y-%m-%d-%H%M')
        )
    else:
        report_name = u'{csv_name}_{timestamp_str}.csv'.format(
            csv_name=csv_name,
            timestamp_str=timestamp.strftime('%Y-%m-%d-%H%M')
        )

    report_store.store_rows(course_id, report_name, rows)
    tracker_emit(csv_name)


def upload_all_courses_progress_csv(_xmodule_instance_args, _entry_id, course_id, task_input, action_name):
    """
    Generate a CSV file containing information about all courses enrolled students progress and grade details.
    """
    student_data = []
    start_time = time()
    start_date = datetime.now(UTC)
    num_reports = 1
    task_progress = TaskProgress(action_name, num_reports, start_time)
    current_step = {'step': 'Calculating progress'}
    task_progress.update_task_state(extra_meta=current_step)
    error = None

    query_features_names = [
        'User ID', 'Email', 'Username', 'First Name', 'Last Name', 'Course ID', 'Course Name', 'Enrollment Status',
        'Enrollment Mode', 'Enrollment Date', 'Progress Percent', 'Grade Percent', 'Letter Grade', 'Completion Date', 'Pass Date',
        'Certificate Eligible', 'Certificate Delivered'
    ]

    query_features = task_input.get('features')
    request_user_id = task_input.get('request_user_id')
    is_course_level = task_input.get('is_course_level')
    request_site_domain = task_input.get('request_site_domain')
    is_site_level = task_input.get('is_site_level')

    if is_course_level:
        csv_name = "current_course_enrolled_users_progress_info"
    else:
        csv_name = "courses_enrolled_users_progress_info"

    if not is_site_level:
        try:
            request_user = User.objects.get(id=request_user_id)
        except User.DoesNotExist:
            logger.error("Unable to generate grand courses report - User does not exist.")

        error, allowed_sites = validate_sites_for_local_admin(request_user)
    else:
        try:
            request_site = Site.objects.get(domain=request_site_domain)
            allowed_sites = [request_site]
        except Site.DoesNotExist:
            error = "Unable to generate grand courses report - Site with domain: {} does not exist.".format(request_site_domain)

    if error:
        # user is normal user - not authorized to view reports
        # OR requested site does not exist
        logger.error(error)
    else:
        student_data = list_all_course_enrolled_users_progress_for_report(allowed_sites, course_id, is_course_level)

    csv_name = get_csv_name(allowed_sites, csv_name, is_site_level)
    header, rows = format_dictlist(student_data, query_features)

    task_progress.attempted = task_progress.succeeded = len(rows)
    task_progress.skipped = task_progress.total - task_progress.attempted

    rows.insert(0, query_features_names)

    current_step = {'step': 'Uploading CSV'}
    task_progress.update_task_state(extra_meta=current_step)

    # Perform the upload
    upload_credits_csv_to_report_store(rows, csv_name, course_id, start_date, False)

    return task_progress.update_task_state(extra_meta=current_step)


def upload_all_site_registered_users_csv(_xmodule_instance_args, _entry_id, course_id, task_input, action_name):
    """
    Generate a CSV file containing information about all active registered users of current site.
    """
    user_data = []
    start_time = time()
    start_date = datetime.now(UTC)
    num_reports = 1
    task_progress = TaskProgress(action_name, num_reports, start_time)
    current_step = {'step': 'Generating users\'s details'}
    task_progress.update_task_state(extra_meta=current_step)
    error = None

    query_features_names = [
        'User ID', 'Email', 'Username', 'First Name', 'Last Name', 'Date Joined', 'Site(s) associated'
    ]

    query_features = task_input.get('features')
    date_joined_start = task_input.get('date_joined_filter_start')
    date_joined_end = task_input.get('date_joined_filter_end')

    date_joined_filter = {
        'start': datetime.strptime(date_joined_start, "%Y-%m-%d").date() if date_joined_start else None,
        'end': datetime.strptime(date_joined_end, "%Y-%m-%d").date() if date_joined_end else None
    }

    request_user_id = task_input.get('request_user_id')
    request_site_domain = task_input.get('request_site_domain')
    is_site_level = task_input.get('is_site_level')
    site = Site.objects.get(domain=request_site_domain)


    csv_name = "registered_users_info"
    csv_name = get_csv_name([site], csv_name, is_site_level)

    user_data = list_all_site_wise_registered_users_for_report(site, is_site_level, date_joined_filter)

    header, rows = format_dictlist(user_data, query_features)

    task_progress.attempted = task_progress.succeeded = len(rows)
    task_progress.skipped = task_progress.total - task_progress.attempted

    rows.insert(0, query_features_names)

    current_step = {'step': 'Uploading CSV'}
    task_progress.update_task_state(extra_meta=current_step)

    # Perform the upload
    upload_credits_csv_to_report_store(rows, csv_name, course_id, start_date, False)

    return task_progress.update_task_state(extra_meta=current_step)
