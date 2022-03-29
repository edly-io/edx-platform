from django.db import transaction
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from opaque_keys.edx.keys import CourseKey
from util.json_request import JsonResponse

from lms.djangoapps.instructor import permissions
from lms.djangoapps.instructor.views.api import (
    require_course_permission,
    common_exceptions_400,
    SUCCESS_MESSAGE_TEMPLATE
)
from openedx.features.clearesult_features.instructor_reports import task_helper


@transaction.non_atomic_requests
@require_POST
@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.CAN_RESEARCH)
@common_exceptions_400
def get_students_credits(request, course_id):
    """
    Initiate generation of a CSV file containing information about
    user earned credits in courses or user accumulative earned credits.

    Responds with JSON
        {"status": "... status message ..."}

    """
    course_key = CourseKey.from_string(course_id)
    csv_type = request.POST.get('csv_type', 'credits')
    if csv_type == 'credits':
        query_features = [
            'username', 'email', 'user_provider_id', 'provider_name', 'provider_short_code',
            'course_id', 'course_name', 'earned_credits', 'grade_percent', 'letter_grade', 'pass_date'
        ]
    else:
        query_features = ['username', 'email', 'user_provider_id', 'provider_name', 'total_earned_credits']

    task_helper.submit_calculate_credits_csv(request, course_key, query_features, csv_type)
    success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=csv_type)
    return JsonResponse({'status': success_status})


@transaction.non_atomic_requests
@require_POST
@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.CAN_RESEARCH)
@common_exceptions_400
def get_all_courses_progress_data(request, course_id):
    """
    Initiate generation of a CSV file containing information about
    all courses enrollment progress and status details.

    Responds with JSON
        {"status": "... status message ..."}
    """
    task_type = 'all_courses_progress'
    course_key = CourseKey.from_string(course_id)
    query_features = [
        'user_id', 'email', 'username', 'first_name', 'last_name', 'course_id', 'course_name', 'enrollment_status',
        'enrollment_mode', 'enrollment_date', 'progress_percent', 'grade_percent', 'letter_grade', 'completion_date', 'pass_date',
        'certificate_eligible', 'certificate_delivered'
    ]

    task_helper.submit_calculate_all_courses_progress_csv(request, course_key, query_features, task_type)
    success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=task_type)
    return JsonResponse({'status': success_status})


@transaction.non_atomic_requests
@require_POST
@ensure_csrf_cookie
@cache_control(no_cache=True, no_store=True, must_revalidate=True)
@require_course_permission(permissions.CAN_RESEARCH)
@common_exceptions_400
def get_registered_users(request, course_id):
    task_type = 'site_wise_registered_users'
    course_key = CourseKey.from_string(course_id)
    query_features = [
        'user_id', 'email', 'username', 'first_name', 'last_name', 'date_joined', 'sites_associated'
    ]

    task_helper.submit_get_registered_users_csv(request, course_key, query_features, task_type)
    success_status = SUCCESS_MESSAGE_TEMPLATE.format(report_type=task_type)
    return JsonResponse({'status': success_status})
