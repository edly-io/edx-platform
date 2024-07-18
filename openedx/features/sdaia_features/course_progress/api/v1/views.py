"""
The User Watch Hours API view - SDAIA Specific.
"""

import logging
import requests

from ccx_keys.locator import CCXLocator
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import F
from django.utils.decorators import method_decorator
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    UserBasedRole,
)
from common.djangoapps.util.disable_rate_limit import can_disable_rate_limit
from edx_rest_framework_extensions.auth.session.authentication import (
    SessionAuthenticationAllowInactiveUser,
)
from lms.djangoapps.badges.models import BadgeAssertion, LeaderboardEntry
from lms.djangoapps.certificates.models import GeneratedCertificate
from openedx.core.djangoapps.cors_csrf.decorators import ensure_csrf_cookie_cross_domain
from openedx.core.djangoapps.enrollments.errors import CourseEnrollmentError
from openedx.core.djangoapps.enrollments.data import get_course_enrollments
from openedx.core.djangoapps.enrollments.views import EnrollmentCrossDomainSessionAuth
from openedx.core.djangoapps.programs.utils import ProgramProgressMeter
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.permissions import ApiKeyHeaderPermissionIsAuthenticated
from openedx.features.sdaia_features.course_progress.utils import (
    get_user_certificates,
)


log = logging.getLogger(__name__)


@can_disable_rate_limit
class UserStatsAPIView(APIView):
    """
    APIView to get the user stats.

    **Example Requests**
        GET /sdaia/api/v1/user_stats

        Response: {
            "watch_hours": 0.00043390860160191856,
            "enrolled_courses": enrolled_courses,
            "enrolled_programs": enrolled_programs,
            "user_certificates": user_certificates,
            "score": score,
        }
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        EnrollmentCrossDomainSessionAuth,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)

    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, request):
        """
        Gets the stats for a user.
        """
        user = request.user
        user_id = user.id
        errors = {}

        ############ WATCH HOURS ############
        try:
            clickhouse_uri = (
                f"{settings.CAIRN_CLICKHOUSE_HTTP_SCHEME}://{settings.CAIRN_CLICKHOUSE_USERNAME}:{settings.CAIRN_CLICKHOUSE_PASSWORD}@"
                f"{settings.CAIRN_CLICKHOUSE_HOST}:{settings.CAIRN_CLICKHOUSE_HTTP_PORT}/?database={settings.CAIRN_CLICKHOUSE_DATABASE}"
            )
            query = f"SELECT SUM(duration) as `Watch time` FROM `openedx`.`video_view_segments` WHERE user_id={user_id};"
            response = requests.get(clickhouse_uri, data=query.encode("utf8"))
            watch_time = float(response.content.decode().strip()) / (60 * 60)
        except Exception as e:
            log.error(
                f"Unable to fetch watch time for user {user_id} due to this exception: {str(e)}"
            )
            watch_time_error = "ERROR: Unable to Connect to Cairn Clickhouse"
            errors["watch_hours"] = watch_time_error
            watch_time = None

        ############ PROGRAMS COUNT ############
        meter = ProgramProgressMeter(request.site, user, mobile_only=False)
        engaged_programs = meter.engaged_programs
        no_of_programs = len(meter.engaged_programs)

        ############ COURSES COUNT ############
        username = user.username
        try:
            enrolled_courses = len(get_course_enrollments(username))
        except CourseEnrollmentError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": f"An error occurred while retrieving enrollments for user {username}"
                },
            )

        ############ USER CERTIFICATES ############
        user_certificates = get_user_certificates(username)

        ############ USER BADGES ############
        user_badges = BadgeAssertion.objects.filter(user=user).values(
            "image_url",
            "assertion_url",
            "created",
            slug=F("badge_class__slug"),
            issuing_component=F("badge_class__issuing_component"),
            display_name=F("badge_class__display_name"),
            course_id=F("badge_class__course_id"),
            description=F("badge_class__description"),
            criteria=F("badge_class__criteria"),
            image=F("badge_class__image"),
        )
        transformed_user_badges = []
        for badge in user_badges:
            badge_image = badge["image"]
            transformed_user_badges.append(
                {
                    "image_url": badge["image_url"],
                    "assertion_url": badge["assertion_url"],
                    "created": badge["created"],
                    "badge_class": {
                        "slug": badge["slug"],
                        "issuing_component": badge["issuing_component"],
                        "display_name": badge["display_name"],
                        "course_id": str(badge["course_id"]),
                        "description": badge["description"],
                        "criteria": badge["criteria"],
                        "image_url": f"{settings.LMS_ROOT_URL}{settings.MEDIA_URL}{badge_image}",
                    },
                }
            )

        ############ USER SCORE ############
        leaderboard = LeaderboardEntry.objects.filter(user=user)
        score = leaderboard and leaderboard.first().score

        ############ Response ############
        return Response(
            status=status.HTTP_200_OK,
            data={
                "watch_hours": watch_time,
                "enrolled_courses": enrolled_courses,
                "enrolled_programs": no_of_programs,
                "score": score,
                "user_certificates": user_certificates,
                "user_badges": transformed_user_badges,
                "errors": errors,
            },
        )


@can_disable_rate_limit
class DashboardStatsAPIView(APIView):
    """
    APIView to get the dashboard stats.

    **Example Requests**
        GET /sdaia/api/v1/dashboard_stats

        Response: {

        }
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)

    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, request):
        """
        Gets the stats for dashboard.
        """
        user = request.user
        users_count = User.objects.all().count()
        certificates_count = GeneratedCertificate.objects.all().count()
        errors = {}

        ############ TOTAL WATCH HOURS ############
        try:
            clickhouse_uri = (
                f"{settings.CAIRN_CLICKHOUSE_HTTP_SCHEME}://{settings.CAIRN_CLICKHOUSE_USERNAME}:{settings.CAIRN_CLICKHOUSE_PASSWORD}@"
                f"{settings.CAIRN_CLICKHOUSE_HOST}:{settings.CAIRN_CLICKHOUSE_HTTP_PORT}/?database={settings.CAIRN_CLICKHOUSE_DATABASE}"
            )
            query = f"SELECT SUM(duration) as `Watch time` FROM `openedx`.`video_view_segments`;"
            response = requests.get(clickhouse_uri, data=query.encode("utf8"))
            watch_time = float(response.content.decode().strip()) / (60 * 60)
        except Exception as e:
            log.error(
                f"Unable to fetch total watch hours due to this exception: {str(e)}"
            )
            watch_time_error = "ERROR: Unable to Connect to Cairn Clickhouse"
            errors["total_watch_time"] = watch_time_error
            watch_time = None

        ############ Response ############
        return Response(
            status=status.HTTP_200_OK,
            data={
                "users_count": users_count,
                "certificates_count": certificates_count,
                "total_watch_time": watch_time,
                "errors": errors,
            },
        )
