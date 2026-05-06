"""
The Enrollment API Views should be simple, lean HTTP endpoints for API access. This should
consist primarily of authentication, request validation, and serialization.

"""

import logging

from django.core.exceptions import (  # lint-amnesty, pylint: disable=wrong-import-order
    ObjectDoesNotExist,
    ValidationError,
)
from django.db import IntegrityError  # lint-amnesty, pylint: disable=wrong-import-order
from django.db.models import Q  # lint-amnesty, pylint: disable=wrong-import-order
from django.utils.decorators import method_decorator  # lint-amnesty, pylint: disable=wrong-import-order
from drf_spectacular.utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiRequest,
    OpenApiResponse,
)
from edx_rest_framework_extensions.auth.jwt.authentication import (
    JwtAuthentication,
)  # lint-amnesty, pylint: disable=wrong-import-order
from edx_rest_framework_extensions.auth.session.authentication import (
    SessionAuthenticationAllowInactiveUser,
)  # lint-amnesty, pylint: disable=wrong-import-order
from edx_rest_framework_extensions.paginators import DefaultPagination  # lint-amnesty, pylint: disable=wrong-import-order
from opaque_keys import InvalidKeyError  # lint-amnesty, pylint: disable=wrong-import-order
from opaque_keys.edx.keys import CourseKey  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework import permissions, status, viewsets  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.decorators import action  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.generics import ListAPIView  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.response import Response  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.throttling import UserRateThrottle  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.views import APIView  # lint-amnesty, pylint: disable=wrong-import-order

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.auth import user_has_role
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed, EnrollmentNotAllowed, User
from common.djangoapps.student.roles import CourseStaffRole, GlobalStaff
from common.djangoapps.util.disable_rate_limit import can_disable_rate_limit
from openedx.core.djangoapps.cors_csrf.authentication import SessionAuthenticationCrossDomainCsrf
from openedx.core.djangoapps.cors_csrf.decorators import ensure_csrf_cookie_cross_domain
from openedx.core.djangoapps.course_groups.cohorts import CourseUserGroup, add_user_to_cohort, get_cohort_by_name
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.embargo import api as embargo_api  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.enrollments import api  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.enrollments.errors import (  # lint-amnesty, pylint: disable=wrong-import-order
    CourseEnrollmentError,
    CourseEnrollmentExistsError,
    CourseModeNotFoundError,
    InvalidEnrollmentAttribute,
)
from openedx.core.djangoapps.enrollments.forms import CourseEnrollmentsApiListForm  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.enrollments.paginators import CourseEnrollmentsApiListPagination  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.enrollments.serializers import (  # lint-amnesty, pylint: disable=wrong-import-order
    CourseEnrollmentAllowedSerializer,
    CourseEnrollmentSerializer,
    CourseEnrollmentsApiListSerializer,
    CourseSerializer,
    UserRolesResponseSerializer,
)
from openedx.core.djangoapps.user_api.accounts.permissions import CanRetireUser
from openedx.core.djangoapps.user_api.models import UserRetirementStatus
from openedx.core.djangoapps.user_api.preferences.api import update_email_opt_in
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.permissions import ApiKeyHeaderPermission, ApiKeyHeaderPermissionIsAuthenticated
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.lib.exceptions import CourseNotFoundError
from openedx.core.lib.log_utils import audit_log
from openedx.features.enterprise_support.api import (
    ConsentApiServiceClient,
    EnterpriseApiException,
    EnterpriseApiServiceClient,
    enterprise_enabled,
)

log = logging.getLogger(__name__)
REQUIRED_ATTRIBUTES = {
    "credit": ["credit:provider_id"],
}


# ADR 0027 — shared OpenAPI parameter and response building blocks
def _path_param(name: str, description: str) -> OpenApiParameter:
    return OpenApiParameter(
        name=name, description=description, required=True, type=str, location=OpenApiParameter.PATH,
    )


def _query_param(name: str, description: str, required: bool = False, type_=str) -> OpenApiParameter:
    return OpenApiParameter(
        name=name, description=description, required=required, type=type_, location=OpenApiParameter.QUERY,
    )


_COURSE_ID_PATH_PARAM = _path_param("course_id", "Course ID (e.g. course-v1:org+course+run).")
_USERNAME_PATH_PARAM = _path_param("username", "Username of the user.")
_USER_QUERY_PARAM = _query_param("user", "Username of the user whose enrollments to list.")
_INCLUDE_EXPIRED_QUERY_PARAM = _query_param(
    "include_expired", "If '1', include expired enrollment modes in the response.",
)
_PAGE_QUERY_PARAM = _query_param("page", "Page number to retrieve. Default 1.")
_PAGE_SIZE_QUERY_PARAM = _query_param("page_size", "Items per page (default 10, max 100).")

_RESP_UNAUTHENTICATED = OpenApiResponse(description="The requester is not authenticated.")
_RESP_FORBIDDEN = OpenApiResponse(description="The requester does not have permission for this operation.")
_RESP_NOT_FOUND = OpenApiResponse(description="The requested resource does not exist.")
_RESP_BAD_REQUEST = OpenApiResponse(description="Invalid request data or parameters.")


class EnrollmentCrossDomainSessionAuth(SessionAuthenticationAllowInactiveUser, SessionAuthenticationCrossDomainCsrf):
    """Session authentication that allows inactive users and cross-domain requests."""

    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class ApiKeyPermissionMixIn:
    """
    This mixin is used to provide a convenience function for doing individual permission checks
    for the presence of API keys.
    """

    def has_api_key_permissions(self, request):
        """
        Checks to see if the request was made by a server with an API key.

        Args:
            request (Request): the request being made into the view

        Return:
            True if the request has been made with a valid API key
            False otherwise
        """
        return ApiKeyHeaderPermission().has_permission(request, self)


class EnrollmentUserThrottle(UserRateThrottle, ApiKeyPermissionMixIn):
    """Limit the number of requests users can make to the enrollment API."""

    # To see how the staff rate limit was selected, see https://github.com/openedx/edx-platform/pull/18360
    THROTTLE_RATES = {
        "user": "40/minute",
        "staff": "120/minute",
    }

    def allow_request(self, request, view):
        # Use a special scope for staff to allow for a separate throttle rate
        user = request.user
        if user.is_authenticated and (user.is_staff or user.is_superuser):
            self.scope = "staff"
            self.rate = self.get_rate()
            self.num_requests, self.duration = self.parse_rate(self.rate)

        return self.has_api_key_permissions(request) or super().allow_request(request, view)


@can_disable_rate_limit
class EnrollmentView(APIView, ApiKeyPermissionMixIn):
    """
    **Use Case**

        Get the user's enrollment status for a course.

    **Example Request**

        GET /api/enrollment/v1/enrollment/{username},{course_id}

    **Response Values**

        If the request for information about the user is successful, an HTTP 200 "OK" response
        is returned.

        The HTTP 200 response has the following values.

        * course_details: A collection that includes the following
          values.

            * course_end: The date and time when the course closes. If
              null, the course never ends.
            * course_id: The unique identifier for the course.
            * course_name: The name of the course.
            * course_modes: An array of data about the enrollment modes
              supported for the course. If the request uses the parameter
              include_expired=1, the array also includes expired
              enrollment modes.

              Each enrollment mode collection includes the following
              values.

                    * currency: The currency of the listed prices.
                    * description: A description of this mode.
                    * expiration_datetime: The date and time after which
                      users cannot enroll in the course in this mode.
                    * min_price: The minimum price for which a user can
                      enroll in this mode.
                    * name: The full name of the enrollment mode.
                    * slug: The short name for the enrollment mode.
                    * suggested_prices: A list of suggested prices for
                      this enrollment mode.

            * course_end: The date and time at which the course closes.  If
              null, the course never ends.
            * course_start: The date and time when the course opens. If
              null, the course opens immediately when it is created.
            * enrollment_end: The date and time after which users cannot
              enroll for the course. If null, the enrollment period never
              ends.
            * enrollment_start: The date and time when users can begin
              enrolling in the course. If null, enrollment opens
              immediately when the course is created.
            * invite_only: A value indicating whether students must be
              invited to enroll in the course. Possible values are true or
              false.

        * created: The date the user account was created.
        * is_active: Whether the enrollment is currently active.
        * mode: The enrollment mode of the user in this course.
        * user: The ID of the user.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseEnrollmentSerializer

    # Since the course about page on the marketing site uses this API to auto-enroll users,
    # we need to support cross-domain CSRF.
    @extend_schema(
        summary="Retrieve a user's enrollment in a course",
        description=(
            "Returns the current user's enrollment for the specified course, or the named user's "
            "enrollment when invoked with the {username},{course_id} URL form (server-to-server or "
            "staff only)."
        ),
        parameters=[_USERNAME_PATH_PARAM, _COURSE_ID_PATH_PARAM],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentSerializer,
                description="Enrollment retrieved successfully (or empty body if no enrollment).",
            ),
            400: _RESP_BAD_REQUEST,
            404: _RESP_NOT_FOUND,
        },
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, request, course_id=None, username=None):
        """Create, read, or update enrollment information for a user.

        HTTP Endpoint for all CRUD operations for a user course enrollment. Allows creation, reading, and
        updates of the current enrollment for a particular course.

        Args:
            request (Request): To get current course enrollment information, a GET request will return
                information for the current user and the specified course.
            course_id (str): URI element specifying the course location. Enrollment information will be
                returned, created, or updated for this particular course.
            username (str): The username associated with this enrollment request.

        Return:
            A JSON serialized representation of the course enrollment.

        """
        username = username or request.user.username

        # TODO Implement proper permissions
        if (
            request.user.username != username
            and not self.has_api_key_permissions(request)
            and not request.user.is_staff
        ):
            # Return a 404 instead of a 403 (Unauthorized). If one user is looking up
            # other users, do not let them deduce the existence of an enrollment.
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": f"No course '{course_id}' found for enrollment"},
            )

        try:
            enrollment = CourseEnrollment.objects.get(user__username=username, course_id=course_key)
        except CourseEnrollment.DoesNotExist:
            return Response(None)
        except CourseEnrollmentError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": (
                        "An error occurred while retrieving enrollments for user "
                        "'{username}' in course '{course_id}'"
                    ).format(username=username, course_id=course_id)
                },
            )

        serializer = self.serializer_class(enrollment)
        return Response(serializer.data)


class EnrollmentUserRolesView(APIView):
    """
    **Use Case**

        Get the roles for the current logged-in user.
        A field is also included to indicate whether or not the user is a global
        staff member.
        If an optional course_id parameter is supplied, the returned roles will be
        filtered to only include roles for the given course.

    **Example Requests**

        GET /api/enrollment/v1/roles/?course_id={course_id}

        course_id: (optional) A course id. The returned roles will be filtered to
        only include roles for the given course.

    **Response Values**

        If the request is successful, an HTTP 200 "OK" response is
        returned along with a collection of user roles for the
        logged-in user, filtered by course_id if given, along with
        whether or not the user is global staff
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        EnrollmentCrossDomainSessionAuth,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = UserRolesResponseSerializer

    @extend_schema(
        summary="List the current user's course roles",
        description=(
            "Returns the list of course-level roles held by the currently logged-in user, plus an "
            "is_staff flag. Optionally filters by course_id."
        ),
        parameters=[_query_param("course_id", "If provided, only roles for this course are returned.")],
        responses={
            200: OpenApiResponse(
                response=UserRolesResponseSerializer,
                description="Roles retrieved successfully.",
            ),
            400: _RESP_BAD_REQUEST,
        },
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, request):
        """
        Gets a list of all roles for the currently logged-in user, filtered by course_id if supplied
        """
        try:
            course_id = request.GET.get("course_id")
            roles_data = api.get_user_roles(request.user.username)
            if course_id:
                roles_data = [role for role in roles_data if str(role.course_id) == course_id]
        except Exception:  # pylint: disable=broad-except
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": ("An error occurred while retrieving roles for user '{username}").format(
                        username=request.user.username
                    )
                },
            )
        serializer = self.serializer_class({
            "roles": list(roles_data),
            "is_staff": request.user.is_staff,
        })
        return Response(serializer.data)


@can_disable_rate_limit
class EnrollmentCourseDetailView(APIView):
    """
    **Use Case**

        Get enrollment details for a course.

        Response values include the course schedule and enrollment modes
        supported by the course. Use the parameter include_expired=1 to
        include expired enrollment modes in the response.

        **Note:** Getting enrollment details for a course does not require
        authentication.

    **Example Requests**

        GET /api/enrollment/v1/course/{course_id}

        GET /api/enrollment/v1/course/{course_id}?include_expired=1

    **Response Values**

        If the request is successful, an HTTP 200 "OK" response is
        returned along with a collection of course enrollments for the
        user or for the newly created enrollment.

        Each course enrollment contains the following values.

            * course_end: The date and time when the course closes. If
              null, the course never ends.
            * course_id: The unique identifier for the course.
            * course_name: The name of the course.
            * course_modes: An array of data about the enrollment modes
              supported for the course. If the request uses the parameter
              include_expired=1, the array also includes expired
              enrollment modes.

              Each enrollment mode collection includes the following
              values.

                    * currency: The currency of the listed prices.
                    * description: A description of this mode.
                    * expiration_datetime: The date and time after which
                      users cannot enroll in the course in this mode.
                    * min_price: The minimum price for which a user can
                      enroll in this mode.
                    * name: The full name of the enrollment mode.
                    * slug: The short name for the enrollment mode.
                    * suggested_prices: A list of suggested prices for
                      this enrollment mode.

            * course_start: The date and time when the course opens. If
              null, the course opens immediately when it is created.
            * enrollment_end: The date and time after which users cannot
              enroll for the course. If null, the enrollment period never
              ends.
            * enrollment_start: The date and time when users can begin
              enrolling in the course. If null, enrollment opens
              immediately when the course is created.
            * invite_only: A value indicating whether students must be
              invited to enroll in the course. Possible values are true or
              false.
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseSerializer

    @extend_schema(
        summary="Get enrollment details for a course",
        description=(
            "Returns the course schedule and the enrollment modes supported by the course. "
            "This endpoint does not require authentication. Use ?include_expired=1 to include "
            "expired enrollment modes."
        ),
        parameters=[_COURSE_ID_PATH_PARAM, _INCLUDE_EXPIRED_QUERY_PARAM],
        responses={
            200: OpenApiResponse(
                response=CourseSerializer,
                description="Course enrollment details retrieved successfully.",
            ),
            400: _RESP_BAD_REQUEST,
        },
    )
    def get(self, request, course_id=None):
        """Read enrollment information for a particular course.

        HTTP Endpoint for retrieving course level enrollment information.

        Args:
            request (Request): To get current course enrollment information, a GET request will return
                information for the specified course.
            course_id (str): URI element specifying the course location. Enrollment information will be
                returned.

        Return:
            A JSON serialized representation of the course enrollment details.

        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": f"No course found for course ID '{course_id}'"},
            )
        try:
            course_overview = CourseOverview.get_from_id(course_key)
        except CourseOverview.DoesNotExist:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": f"No course found for course ID '{course_id}'"},
            )
        include_expired = bool(request.GET.get("include_expired", ""))
        serializer = self.serializer_class(course_overview, include_expired=include_expired)
        return Response(serializer.data)


# DEPRECATED (ADR 0028): Use EnrollmentViewSet.unenroll action instead. Will be removed after one named release.
class UnenrollmentView(APIView):
    """
    **Use Cases**

        * Unenroll a single user from all courses.

          This command can only be issued by a privileged service user.

    **Example Requests**

        POST /api/enrollment/v1/enrollment {
            "username": "username12345"
        }

    **POST Parameters**

        A POST request must include the following parameter.

        * username: The username of the user being unenrolled.
        This will never match the username from the request,
        since the request is issued as a privileged service user.

    **POST Response Values**

        If the user has not requested retirement and does not have a retirement
        request status, the request returns an HTTP 404 "Does Not Exist" response.

        If the user is already unenrolled from all courses, the request returns
        an HTTP 204 "No Content" response.

        If an unexpected error occurs, the request returns an HTTP 500 response.

        If the request is successful, an HTTP 200 "OK" response is
        returned along with a list of all courses from which the user was unenrolled.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        CanRetireUser,
    )
    serializer_class = CourseEnrollmentSerializer

    @extend_schema(
        operation_id="enrollment_v1_unenroll_deprecated",
        summary="Unenroll a user from all courses (deprecated)",
        description=(
            "Deprecated. Use POST /api/enrollment/v1/enrollment/unenroll/ "
            "(EnrollmentViewSet.unenroll action) instead. Privileged retirement-pipeline use only."
        ),
        request=OpenApiRequest(
            request={
                "type": "object",
                "properties": {"username": {"type": "string"}},
                "required": ["username"],
            }
        ),
        responses={
            200: OpenApiResponse(description="List of courses from which the user was unenrolled."),
            204: OpenApiResponse(description="User has no active enrollments."),
            404: OpenApiResponse(description="Username not specified or no retirement status for user."),
            500: OpenApiResponse(description="Unexpected error during unenrollment."),
        },
        deprecated=True,
    )
    def post(self, request):
        """
        Unenrolls the specified user from all courses.
        """
        try:
            # Get the username from the request.
            username = request.data["username"]
            # Ensure that a retirement request status row exists for this username.
            UserRetirementStatus.get_retirement_for_retirement_action(username)
            active_enrollments = CourseEnrollment.objects.filter(
                user__username=username, is_active=True
            )
            if not active_enrollments.exists():
                return Response(status=status.HTTP_204_NO_CONTENT)
            return Response(api.unenroll_user_from_all_courses(username))
        except KeyError:
            return Response("Username not specified.", status=status.HTTP_404_NOT_FOUND)
        except UserRetirementStatus.DoesNotExist:
            return Response("No retirement request status for username.", status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ADR 0028 – consolidated from EnrollmentListView, UnenrollmentView, EnrollmentAllowedView
@can_disable_rate_limit
class EnrollmentViewSet(viewsets.ViewSet, ApiKeyPermissionMixIn):
    """
    DRF ViewSet for the Enrollment API.

    Consolidates EnrollmentListView, UnenrollmentView, and EnrollmentAllowedView into a single
    ViewSet registered via DefaultRouter per ADR 0028.

    Actions:
        list        GET  /enrollment/              List enrollments for the current user.
        create      POST /enrollment/              Enroll the current user in a course.
        unenroll    POST /enrollment/unenroll/     Unenroll a user from all courses (retirement pipeline).
        allowed  GET/POST/DELETE /enrollment/enrollment_allowed/  Manage CourseEnrollmentAllowed records.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        EnrollmentCrossDomainSessionAuth,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseEnrollmentSerializer
    pagination_class = DefaultPagination  # ADR 0032

    def get_serializer_class(self):
        """Return CourseEnrollmentAllowedSerializer for the 'allowed' action, else the default."""
        if self.action == "allowed":
            return CourseEnrollmentAllowedSerializer
        return self.serializer_class

    def get_serializer(self, *args, **kwargs):
        """Instantiate and return the appropriate serializer for this action."""
        return self.get_serializer_class()(*args, **kwargs)

    @extend_schema(
        summary="List enrollments for a user (paginated)",
        description=(
            "Returns a paginated list of enrollments for the currently logged-in user, or for the "
            "user named by the 'user' query parameter (staff/admin/api-key access required to view "
            "another user's enrollments — otherwise filtered to courses the requester staffs)."
        ),
        parameters=[_USER_QUERY_PARAM, _PAGE_QUERY_PARAM, _PAGE_SIZE_QUERY_PARAM],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentSerializer(many=True),
                description="Paginated enrollment list.",
            ),
            401: _RESP_UNAUTHENTICATED,
        },
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def list(self, request):
        """Gets a list of all course enrollments for a user.

        Returns a paginated list for the currently logged-in user, or for the user named by the
        'user' GET parameter. If the username does not match that of the currently logged-in user,
        only courses for which the currently logged-in user has the Staff or Admin role are listed.

        **Pagination Parameters**

            - ``page`` (int): Page number to retrieve. Default is 1.
            - ``page_size`` (int): Items per page. Default is 10, max is 100.

        **Response Envelope**

            - ``count`` (int): Total number of results.
            - ``num_pages`` (int): Total number of pages.
            - ``current_page`` (int): The current page number.
            - ``start`` (int): The 0-based index of the first item on this page.
            - ``next`` (str|null): URL for the next page, or null.
            - ``previous`` (str|null): URL for the previous page, or null.
            - ``results`` (list): The list of enrollments for this page.
        """
        username = request.GET.get("user", request.user.username)
        enrollments = CourseEnrollment.objects.filter(
            user__username=username
        ).select_related("user", "course")  # "course" is the FK field; "course_overview" is a property
        paginator = self.pagination_class()
        if (
            username == request.user.username
            or GlobalStaff().has_user(request.user)
            or self.has_api_key_permissions(request)
        ):
            page = paginator.paginate_queryset(enrollments, request, view=self)
            return paginator.get_paginated_response(self.get_serializer(page, many=True).data)
        filtered_enrollments = [
            enrollment for enrollment in enrollments
            if user_has_role(request.user, CourseStaffRole(enrollment.course_id))
        ]
        page = paginator.paginate_queryset(filtered_enrollments, request, view=self)
        return paginator.get_paginated_response(self.get_serializer(page, many=True).data)

    @extend_schema(
        summary="Create or update an enrollment",
        description=(
            "Enrolls a user in a course. Server-to-server calls may deactivate or modify the mode "
            "of existing enrollments; all other requests go through add_enrollment(), which creates "
            "or reactivates enrollments. The request body must include course_details.course_id."
        ),
        request=OpenApiRequest(request=CourseEnrollmentSerializer),
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentSerializer,
                description="Enrollment created, reactivated, or updated successfully.",
            ),
            400: _RESP_BAD_REQUEST,
            403: _RESP_FORBIDDEN,
            404: _RESP_NOT_FOUND,
            406: OpenApiResponse(description="The specified user does not exist."),
        },
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def create(self, request):
        # pylint: disable=too-many-statements
        """Enrolls the currently logged-in user in a course.

        Server-to-server calls may deactivate or modify the mode of existing enrollments. All other
        requests go through add_enrollment(), which allows creation and reactivation of enrollments.
        """
        username = request.data.get("user")
        course_id = request.data.get("course_details", {}).get("course_id")

        if not course_id:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": "Course ID must be specified to create a new enrollment."},
            )

        try:
            course_id = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST, data={"message": f"No course '{course_id}' found for enrollment"}
            )

        mode = request.data.get("mode")

        has_api_key_permissions = self.has_api_key_permissions(request)

        if (
            username
            and username != request.user.username
            and not has_api_key_permissions
            and not GlobalStaff().has_user(request.user)
        ):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not username:
            email = request.data.get("email")
            if email:
                if not has_api_key_permissions and not GlobalStaff().has_user(request.user):
                    return Response(status=status.HTTP_404_NOT_FOUND)
                try:
                    username = User.objects.get(email=email).username
                except ObjectDoesNotExist:
                    return Response(
                        status=status.HTTP_406_NOT_ACCEPTABLE,
                        data={"message": f"The user with the email address {email} does not exist."},
                    )
            else:
                username = request.user.username

        if (
            mode not in (CourseMode.AUDIT, CourseMode.HONOR, None)
            and not has_api_key_permissions
            and not GlobalStaff().has_user(request.user)
        ):
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={
                    "message": "User does not have permission to create enrollment with mode [{mode}].".format(
                        mode=mode
                    )
                },
            )

        try:
            user = User.objects.get(username=username)
        except ObjectDoesNotExist:
            return Response(
                status=status.HTTP_406_NOT_ACCEPTABLE, data={"message": f"The user {username} does not exist."}
            )

        embargo_response = embargo_api.get_embargo_response(request, course_id, user)

        if embargo_response:
            return embargo_response

        try:
            is_active = request.data.get("is_active")
            if is_active is not None and not isinstance(is_active, bool):
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={"message": ("'{value}' is an invalid enrollment activation status.").format(value=is_active)},
                )

            explicit_linked_enterprise = request.data.get("linked_enterprise_customer")
            if explicit_linked_enterprise and has_api_key_permissions and enterprise_enabled():
                enterprise_api_client = EnterpriseApiServiceClient()
                consent_client = ConsentApiServiceClient()
                try:
                    enterprise_api_client.post_enterprise_course_enrollment(username, str(course_id))
                except EnterpriseApiException as error:
                    log.exception(
                        "An unexpected error occurred while creating the new EnterpriseCourseEnrollment "
                        "for user [%s] in course run [%s]",
                        username,
                        course_id,
                    )
                    raise CourseEnrollmentError(str(error))  # lint-amnesty, pylint: disable=raise-missing-from
                kwargs = {
                    "username": username,
                    "course_id": str(course_id),
                    "enterprise_customer_uuid": explicit_linked_enterprise,
                }
                consent_client.provide_consent(**kwargs)

            enrollment_attributes = request.data.get("enrollment_attributes")
            force_enrollment = request.data.get("force_enrollment")
            if force_enrollment is not None and not isinstance(force_enrollment, bool):
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={
                        "message": ("'{value}' is an invalid force enrollment status.").format(value=force_enrollment)
                    },
                )
            force_enrollment = force_enrollment and GlobalStaff().has_user(request.user)
            enrollment = api.get_enrollment(username, str(course_id))
            mode_changed = enrollment and mode is not None and enrollment["mode"] != mode
            active_changed = enrollment and is_active is not None and enrollment["is_active"] != is_active
            missing_attrs = []
            if enrollment_attributes:
                actual_attrs = ["{namespace}:{name}".format(**attr) for attr in enrollment_attributes]
                missing_attrs = set(REQUIRED_ATTRIBUTES.get(mode, [])) - set(actual_attrs)
            if (GlobalStaff().has_user(request.user) or has_api_key_permissions) and (mode_changed or active_changed):
                if mode_changed and active_changed and not is_active:
                    msg = "Enrollment mode mismatch: active mode={}, requested mode={}. Won't deactivate.".format(
                        enrollment["mode"], mode
                    )
                    log.warning(msg)
                    return Response(status=status.HTTP_400_BAD_REQUEST, data={"message": msg})

                if missing_attrs:
                    msg = "Missing enrollment attributes: requested mode={} required attributes={}".format(
                        mode, REQUIRED_ATTRIBUTES.get(mode)
                    )
                    log.warning(msg)
                    return Response(status=status.HTTP_400_BAD_REQUEST, data={"message": msg})

                response = api.update_enrollment(
                    username,
                    str(course_id),
                    mode=mode,
                    is_active=is_active,
                    enrollment_attributes=enrollment_attributes,
                    include_expired=has_api_key_permissions,
                )
            else:
                response = api.add_enrollment(
                    username,
                    str(course_id),
                    mode=mode,
                    is_active=is_active,
                    enrollment_attributes=enrollment_attributes,
                    enterprise_uuid=request.data.get("enterprise_uuid"),
                    force_enrollment=force_enrollment,
                    include_expired=force_enrollment,
                )

            cohort_name = request.data.get("cohort")
            if cohort_name is not None:
                cohort = get_cohort_by_name(course_id, cohort_name)
                try:
                    add_user_to_cohort(cohort, user)
                except ValueError:
                    log.exception("Cohort re-addition")
            email_opt_in = request.data.get("email_opt_in", None)
            if email_opt_in is not None:
                org = course_id.org
                update_email_opt_in(request.user, org, email_opt_in)

            log.info("The user [%s] has already been enrolled in course run [%s].", username, course_id)
            return Response(response)

        except InvalidEnrollmentAttribute as error:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": str(error),
                    "localizedMessage": str(error),
                }
            )
        except EnrollmentNotAllowed as error:
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={
                    "message": str(error),
                    "localizedMessage": str(error),
                }
            )
        except CourseModeNotFoundError as error:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": (
                        "The [{mode}] course mode is expired or otherwise unavailable for course run [{course_id}]."
                    ).format(mode=mode, course_id=course_id),
                    "course_details": error.data,
                },
            )
        except CourseNotFoundError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST, data={"message": f"No course '{course_id}' found for enrollment"}
            )
        except CourseEnrollmentExistsError as error:
            log.warning("An enrollment already exists for user [%s] in course run [%s].", username, course_id)
            return Response(data=error.enrollment)
        except CourseEnrollmentError:
            log.exception(
                "An error occurred while creating the new course enrollment for user [%s] in course run [%s]",
                username,
                course_id,
            )
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": (
                        "An error occurred while creating the new course enrollment for user "
                        "'{username}' in course '{course_id}'"
                    ).format(username=username, course_id=course_id)
                },
            )
        except CourseUserGroup.DoesNotExist:
            log.exception("Missing cohort [%s] in course run [%s]", cohort_name, course_id)
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": "An error occured while adding to cohort [%s]" % cohort_name},
            )
        finally:
            if has_api_key_permissions:
                try:
                    current_enrollment_obj = CourseEnrollment.objects.get(
                        user__username=username, course_id=course_id
                    )
                    actual_mode = current_enrollment_obj.mode
                    actual_activation = current_enrollment_obj.is_active
                except CourseEnrollment.DoesNotExist:
                    actual_mode = None
                    actual_activation = None
                audit_log(
                    "enrollment_change_requested",
                    course_id=str(course_id),
                    requested_mode=mode,
                    actual_mode=actual_mode,
                    requested_activation=is_active,
                    actual_activation=actual_activation,
                    user_id=user.id,
                )

    @extend_schema(
        summary="Unenroll a user from all courses (retirement)",
        description=(
            "Privileged retirement-pipeline use only. Unenrolls the named user from every active "
            "enrollment. The request must be made by a service user with CanRetireUser permission, "
            "not the user being unenrolled."
        ),
        request=OpenApiRequest(
            request={
                "type": "object",
                "properties": {"username": {"type": "string"}},
                "required": ["username"],
            }
        ),
        responses={
            200: OpenApiResponse(description="List of courses from which the user was unenrolled."),
            204: OpenApiResponse(description="User has no active enrollments."),
            404: OpenApiResponse(description="Username not specified or no retirement status for user."),
            500: OpenApiResponse(description="Unexpected error during unenrollment."),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="unenroll",
        permission_classes=[permissions.IsAuthenticated, CanRetireUser],
    )
    def unenroll(self, request):
        """Unenrolls the specified user from all courses.

        Privileged retirement-pipeline use only. The request must be made by a service user
        with CanRetireUser permission, not the user being unenrolled.
        """
        try:
            username = request.data["username"]
            UserRetirementStatus.get_retirement_for_retirement_action(username)
            active_enrollments = CourseEnrollment.objects.filter(
                user__username=username, is_active=True
            )
            if not active_enrollments.exists():
                return Response(status=status.HTTP_204_NO_CONTENT)
            return Response(api.unenroll_user_from_all_courses(username))
        except KeyError:
            return Response("Username not specified.", status=status.HTTP_404_NOT_FOUND)
        except UserRetirementStatus.DoesNotExist:
            return Response("No retirement request status for username.", status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @extend_schema(
        summary="Manage CourseEnrollmentAllowed records (admin-only)",
        description=(
            "GET lists allowed enrollments for an email; POST creates a new one; DELETE removes one "
            "by email + course_id. Admin-only."
        ),
        request=OpenApiRequest(request=CourseEnrollmentAllowedSerializer),
        parameters=[_query_param("email", "Email to query (GET only). Defaults to the requester's email.")],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentAllowedSerializer(many=True),
                description="GET success — list of allowed enrollments for the email.",
            ),
            201: OpenApiResponse(
                response=CourseEnrollmentAllowedSerializer,
                description="POST success — allowed enrollment created.",
            ),
            204: OpenApiResponse(description="DELETE success — allowed enrollment deleted."),
            400: _RESP_BAD_REQUEST,
            404: OpenApiResponse(description="DELETE: allowed enrollment not found for the given email/course."),
            409: OpenApiResponse(description="POST: allowed enrollment already exists for this email/course."),
        },
    )
    @action(
        detail=False,
        methods=["get", "post", "delete"],
        url_path="enrollment_allowed",
        permission_classes=[permissions.IsAdminUser],
        throttle_classes=[EnrollmentUserThrottle],
    )
    def allowed(self, request):
        """Retrieve, create, or delete CourseEnrollmentAllowed records. Admin-only.

        GET    /enrollment/enrollment_allowed/?email=<email>   List allowed enrollments for an email.
        POST   /enrollment/enrollment_allowed/                  Create a new allowed enrollment.
        DELETE /enrollment/enrollment_allowed/                  Delete an existing allowed enrollment.
        """
        if request.method == "GET":
            user_email = request.query_params.get("email") or request.user.email
            enrollments_allowed = CourseEnrollmentAllowed.objects.filter(email=user_email)
            return Response(
                status=status.HTTP_200_OK,
                data=self.get_serializer(enrollments_allowed, many=True).data,
            )

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(status=status.HTTP_400_BAD_REQUEST, data=serializer.errors)

        if request.method == "POST":
            try:
                enrollment_allowed = serializer.save()
            except IntegrityError:
                return Response(
                    status=status.HTTP_409_CONFLICT,
                    data={
                        "message": (
                            f"An enrollment allowed with email {serializer.validated_data.get('email')} "
                            f"and course {serializer.validated_data.get('course_id')} already exists."
                        )
                    },
                )
            return Response(
                status=status.HTTP_201_CREATED,
                data=self.get_serializer(enrollment_allowed).data,
            )

        # DELETE
        email = serializer.validated_data.get("email")
        course_id = serializer.validated_data.get("course_id")
        try:
            CourseEnrollmentAllowed.objects.get(email=email, course_id=course_id).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ObjectDoesNotExist:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
                data={"message": f"An enrollment allowed with email {email} and course {course_id} doesn't exists."},
            )


# DEPRECATED (ADR 0028): Use EnrollmentViewSet instead. Will be removed after one named release.
@can_disable_rate_limit
class EnrollmentListView(APIView, ApiKeyPermissionMixIn):
    """
    **Use Cases**

        * Get a list of all course enrollments for the currently signed in user.

        * Enroll the currently signed in user in a course.

          Currently a user can use this command only to enroll the
          user in the default course mode. If this is not
          supported for the course, the request fails and returns
          the available modes.

          This command can use a server-to-server call to enroll a user in
          other modes, such as "verified", "professional", or "credit". If
          the mode is not supported for the course, the request will fail
          and return the available modes.

          You can include other parameters as enrollment attributes for a
          specific course mode. For example, for credit mode, you can
          include the following parameters to specify the credit provider
          attribute.

          * namespace: credit
          * name: provider_id
          * value: institution_name

    **Example Requests**

        GET /api/enrollment/v1/enrollment

        POST /api/enrollment/v1/enrollment {

            "mode": "credit",
            "course_details":{"course_id": "edX/DemoX/Demo_Course"},
            "enrollment_attributes":[{"namespace": "credit","name": "provider_id","value": "hogwarts",},]

        }

        **POST Parameters**

          A POST request can include the following parameters.

          * user: Optional. The username of the currently logged in user.
            You cannot use the command to enroll a different user.

          * mode: Optional. The course mode for the enrollment. Individual
            users cannot upgrade their enrollment mode from the default. Only
            server-to-server requests can enroll with other modes.

          * is_active: Optional. A Boolean value indicating whether the
            enrollment is active. Only server-to-server requests are
            allowed to deactivate an enrollment.

          * course details: A collection that includes the following
            information.

              * course_id: The unique identifier for the course.

          * email_opt_in: Optional. A Boolean value that indicates whether
            the user wants to receive email from the organization that runs
            this course.

          * enrollment_attributes: A dictionary that contains the following
            values.

              * namespace: Namespace of the attribute
              * name: Name of the attribute
              * value: Value of the attribute

          * is_active: Optional. A Boolean value that indicates whether the
            enrollment is active. Only server-to-server requests can
            deactivate an enrollment.

          * mode: Optional. The course mode for the enrollment. Individual
            users cannot upgrade their enrollment mode from the default. Only
            server-to-server requests can enroll with other modes.

          * user: Optional. The user ID of the currently logged in user. You
            cannot use the command to enroll a different user.

          * enterprise_course_consent: Optional. A Boolean value that
            indicates the consent status for an EnterpriseCourseEnrollment
            to be posted to the Enterprise service.

    **GET Response Values**

        If an unspecified error occurs when the user tries to obtain a
        learner's enrollments, the request returns an HTTP 400 "Bad
        Request" response.

        If the user does not have permission to view enrollment data for
        the requested learner, the request returns an HTTP 404 "Not Found"
        response.

    **POST Response Values**

         If the user does not specify a course ID, the specified course
         does not exist, or the is_active status is invalid, the request
         returns an HTTP 400 "Bad Request" response.

         If a user who is not an admin tries to upgrade a learner's course
         mode, the request returns an HTTP 403 "Forbidden" response.

         If the specified user does not exist, the request returns an HTTP
         406 "Not Acceptable" response.

    **GET and POST Response Values**

        If the request is successful, an HTTP 200 "OK" response is
        returned along with a collection of course enrollments for the
        user or for the newly created enrollment.

        Each course enrollment contains the following values.

        * course_details: A collection that includes the following
          values.

            * course_end: The date and time when the course closes. If
              null, the course never ends.

            * course_id: The unique identifier for the course.

            * course_name: The name of the course.

            * course_modes: An array of data about the enrollment modes
              supported for the course. If the request uses the parameter
              include_expired=1, the array also includes expired
              enrollment modes.

              Each enrollment mode collection includes the following
              values.

              * currency: The currency of the listed prices.

              * description: A description of this mode.

              * expiration_datetime: The date and time after which users
                cannot enroll in the course in this mode.

              * min_price: The minimum price for which a user can enroll in
                this mode.

              * name: The full name of the enrollment mode.

              * slug: The short name for the enrollment mode.

              * suggested_prices: A list of suggested prices for this
                enrollment mode.

            * course_start: The date and time when the course opens. If
              null, the course opens immediately when it is created.

            * enrollment_end: The date and time after which users cannot
              enroll for the course. If null, the enrollment period never
              ends.

            * enrollment_start: The date and time when users can begin
              enrolling in the course. If null, enrollment opens
              immediately when the course is created.

            * invite_only: A value indicating whether students must be
              invited to enroll in the course. Possible values are true or
              false.

         * created: The date the user account was created.

         * is_active: Whether the enrollment is currently active.

         * mode: The enrollment mode of the user in this course.

         * user: The username of the user.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        EnrollmentCrossDomainSessionAuth,
    )
    permission_classes = (ApiKeyHeaderPermissionIsAuthenticated,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseEnrollmentSerializer

    # Since the course about page on the marketing site
    # uses this API to auto-enroll users, we need to support
    # cross-domain CSRF.
    @extend_schema(
        operation_id="enrollment_v1_enrollment_list_deprecated",
        summary="List enrollments for a user (deprecated)",
        description=(
            "Deprecated. Use GET /api/enrollment/v1/enrollment/ (EnrollmentViewSet.list) instead. "
            "This legacy endpoint returns an unpaginated list."
        ),
        parameters=[_USER_QUERY_PARAM],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentSerializer(many=True),
                description="Enrollments retrieved successfully.",
            ),
            401: _RESP_UNAUTHENTICATED,
        },
        deprecated=True,
    )
    @method_decorator(ensure_csrf_cookie_cross_domain)
    def get(self, request):
        """Gets a list of all course enrollments for a user.

        Returns a list for the currently logged in user, or for the user named by the 'user' GET
        parameter. If the username does not match that of the currently logged in user, only
        courses for which the currently logged in user has the Staff or Admin role are listed.
        As a result, a course team member can find out which of their own courses a particular
        learner is enrolled in.

        Only the Staff or Admin role (granted on the Django administrative console as the staff
        or instructor permission) in individual courses gives the requesting user access to
        enrollment data. Permissions granted at the organizational level do not give a user
        access to enrollment data for all of that organization's courses.

        Users who have the global staff permission can access all enrollment data for all
        courses.
        """
        username = request.GET.get("user", request.user.username)
        enrollments = CourseEnrollment.objects.filter(
            user__username=username
        ).select_related("user", "course_overview")
        if (
            username == request.user.username
            or GlobalStaff().has_user(request.user)
            or self.has_api_key_permissions(request)
        ):
            serializer = self.serializer_class(enrollments, many=True)
            return Response(serializer.data)
        filtered_enrollments = [
            enrollment for enrollment in enrollments
            if user_has_role(request.user, CourseStaffRole(enrollment.course_id))
        ]
        serializer = self.serializer_class(filtered_enrollments, many=True)
        return Response(serializer.data)

    @extend_schema(
        operation_id="enrollment_v1_enrollment_create_deprecated",
        summary="Create or update an enrollment (deprecated)",
        description=(
            "Deprecated. Use POST /api/enrollment/v1/enrollment/ (EnrollmentViewSet.create) instead."
        ),
        request=OpenApiRequest(request=CourseEnrollmentSerializer),
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentSerializer,
                description="Enrollment created, reactivated, or updated successfully.",
            ),
            400: _RESP_BAD_REQUEST,
            403: _RESP_FORBIDDEN,
            404: _RESP_NOT_FOUND,
            406: OpenApiResponse(description="The specified user does not exist."),
        },
        deprecated=True,
    )
    def post(self, request):
        # pylint: disable=too-many-statements
        """Enrolls the currently logged-in user in a course.

        Server-to-server calls may deactivate or modify the mode of existing enrollments. All other requests
        go through `add_enrollment()`, which allows creation of new and reactivation of old enrollments.
        """
        # Get the User, Course ID, and Mode from the request.

        username = request.data.get("user")
        course_id = request.data.get("course_details", {}).get("course_id")

        if not course_id:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": "Course ID must be specified to create a new enrollment."},
            )

        try:
            course_id = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST, data={"message": f"No course '{course_id}' found for enrollment"}
            )

        mode = request.data.get("mode")

        has_api_key_permissions = self.has_api_key_permissions(request)

        # Check that the user specified is either the same user, or this is a server-to-server request.
        if (
            username
            and username != request.user.username
            and not has_api_key_permissions
            and not GlobalStaff().has_user(request.user)
        ):
            # Return a 404 instead of a 403 (Unauthorized). If one user is looking up
            # other users, do not let them deduce the existence of an enrollment.
            return Response(status=status.HTTP_404_NOT_FOUND)

        # A provided user has priority over a provided email.
        # Fallback on request user if neither is provided.
        if not username:
            email = request.data.get("email")
            if email:
                # Only server-to-server or staff users can use the email for the request.
                if not has_api_key_permissions and not GlobalStaff().has_user(request.user):
                    return Response(status=status.HTTP_404_NOT_FOUND)
                try:
                    username = User.objects.get(email=email).username
                except ObjectDoesNotExist:
                    return Response(
                        status=status.HTTP_406_NOT_ACCEPTABLE,
                        data={"message": f"The user with the email address {email} does not exist."},
                    )
            else:
                username = request.user.username

        if (
            mode not in (CourseMode.AUDIT, CourseMode.HONOR, None)
            and not has_api_key_permissions
            and not GlobalStaff().has_user(request.user)
        ):
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={
                    "message": "User does not have permission to create enrollment with mode [{mode}].".format(
                        mode=mode
                    )
                },
            )

        try:
            # Lookup the user, instead of using request.user, since request.user may not match the username POSTed.
            user = User.objects.get(username=username)
        except ObjectDoesNotExist:
            return Response(
                status=status.HTTP_406_NOT_ACCEPTABLE, data={"message": f"The user {username} does not exist."}
            )

        embargo_response = embargo_api.get_embargo_response(request, course_id, user)

        if embargo_response:
            return embargo_response

        try:
            is_active = request.data.get("is_active")
            # Check if the requested activation status is None or a Boolean
            if is_active is not None and not isinstance(is_active, bool):
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={"message": ("'{value}' is an invalid enrollment activation status.").format(value=is_active)},
                )

            explicit_linked_enterprise = request.data.get("linked_enterprise_customer")
            if explicit_linked_enterprise and has_api_key_permissions and enterprise_enabled():
                enterprise_api_client = EnterpriseApiServiceClient()
                consent_client = ConsentApiServiceClient()
                try:
                    enterprise_api_client.post_enterprise_course_enrollment(username, str(course_id))
                except EnterpriseApiException as error:
                    log.exception(
                        "An unexpected error occurred while creating the new EnterpriseCourseEnrollment "
                        "for user [%s] in course run [%s]",
                        username,
                        course_id,
                    )
                    raise CourseEnrollmentError(str(error))  # lint-amnesty, pylint: disable=raise-missing-from
                kwargs = {
                    "username": username,
                    "course_id": str(course_id),
                    "enterprise_customer_uuid": explicit_linked_enterprise,
                }
                consent_client.provide_consent(**kwargs)

            enrollment_attributes = request.data.get("enrollment_attributes")
            force_enrollment = request.data.get("force_enrollment")
            # Check if the force enrollment status is None or a Boolean
            if force_enrollment is not None and not isinstance(force_enrollment, bool):
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={
                        "message": ("'{value}' is an invalid force enrollment status.").format(value=force_enrollment)
                    },
                )
            # Only a staff user role can enroll a user forcefully
            force_enrollment = force_enrollment and GlobalStaff().has_user(request.user)
            enrollment = api.get_enrollment(username, str(course_id))
            mode_changed = enrollment and mode is not None and enrollment["mode"] != mode
            active_changed = enrollment and is_active is not None and enrollment["is_active"] != is_active
            missing_attrs = []
            if enrollment_attributes:
                actual_attrs = ["{namespace}:{name}".format(**attr) for attr in enrollment_attributes]
                missing_attrs = set(REQUIRED_ATTRIBUTES.get(mode, [])) - set(actual_attrs)
            if (GlobalStaff().has_user(request.user) or has_api_key_permissions) and (mode_changed or active_changed):
                if mode_changed and active_changed and not is_active:
                    # if the requester wanted to deactivate but specified the wrong mode, fail
                    # the request (on the assumption that the requester had outdated information
                    # about the currently active enrollment).
                    msg = "Enrollment mode mismatch: active mode={}, requested mode={}. Won't deactivate.".format(
                        enrollment["mode"], mode
                    )
                    log.warning(msg)
                    return Response(status=status.HTTP_400_BAD_REQUEST, data={"message": msg})

                if missing_attrs:
                    msg = "Missing enrollment attributes: requested mode={} required attributes={}".format(
                        mode, REQUIRED_ATTRIBUTES.get(mode)
                    )
                    log.warning(msg)
                    return Response(status=status.HTTP_400_BAD_REQUEST, data={"message": msg})

                response = api.update_enrollment(
                    username,
                    str(course_id),
                    mode=mode,
                    is_active=is_active,
                    enrollment_attributes=enrollment_attributes,
                    # If we are updating enrollment by authorized api caller, we should allow expired modes
                    include_expired=has_api_key_permissions,
                )
            else:
                # Will reactivate inactive enrollments.
                response = api.add_enrollment(
                    username,
                    str(course_id),
                    mode=mode,
                    is_active=is_active,
                    enrollment_attributes=enrollment_attributes,
                    enterprise_uuid=request.data.get("enterprise_uuid"),
                    force_enrollment=force_enrollment,
                    # If we are creating enrollment by staff user with force_enrollment, we should allow expired modes
                    include_expired=force_enrollment,
                )

            cohort_name = request.data.get("cohort")
            if cohort_name is not None:
                cohort = get_cohort_by_name(course_id, cohort_name)
                try:
                    add_user_to_cohort(cohort, user)
                except ValueError:
                    # user already in cohort, probably because they were un-enrolled and re-enrolled
                    log.exception("Cohort re-addition")
            email_opt_in = request.data.get("email_opt_in", None)
            if email_opt_in is not None:
                org = course_id.org
                update_email_opt_in(request.user, org, email_opt_in)

            log.info("The user [%s] has already been enrolled in course run [%s].", username, course_id)
            return Response(response)

        except InvalidEnrollmentAttribute as error:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": str(error),
                    "localizedMessage": str(error),
                }
            )
        except EnrollmentNotAllowed as error:
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={
                    "message": str(error),
                    "localizedMessage": str(error),
                }
            )
        except CourseModeNotFoundError as error:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": (
                        "The [{mode}] course mode is expired or otherwise unavailable for course run [{course_id}]."
                    ).format(mode=mode, course_id=course_id),
                    "course_details": error.data,
                },
            )
        except CourseNotFoundError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST, data={"message": f"No course '{course_id}' found for enrollment"}
            )
        except CourseEnrollmentExistsError as error:
            log.warning("An enrollment already exists for user [%s] in course run [%s].", username, course_id)
            return Response(data=error.enrollment)
        except CourseEnrollmentError:
            log.exception(
                "An error occurred while creating the new course enrollment for user [%s] in course run [%s]",
                username,
                course_id,
            )
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": (
                        "An error occurred while creating the new course enrollment for user "
                        "'{username}' in course '{course_id}'"
                    ).format(username=username, course_id=course_id)
                },
            )

        except CourseUserGroup.DoesNotExist:
            log.exception("Missing cohort [%s] in course run [%s]", cohort_name, course_id)
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": "An error occured while adding to cohort [%s]" % cohort_name},
            )
        finally:
            # Assumes that the ecommerce service uses an API key to authenticate.
            if has_api_key_permissions:
                try:
                    current_enrollment_obj = CourseEnrollment.objects.get(
                        user__username=username, course_id=course_id
                    )
                    actual_mode = current_enrollment_obj.mode
                    actual_activation = current_enrollment_obj.is_active
                except CourseEnrollment.DoesNotExist:
                    actual_mode = None
                    actual_activation = None
                audit_log(
                    "enrollment_change_requested",
                    course_id=str(course_id),
                    requested_mode=mode,
                    actual_mode=actual_mode,
                    requested_activation=is_active,
                    actual_activation=actual_activation,
                    user_id=user.id,
                )


@extend_schema_view(
    get=extend_schema(
        summary="List all course enrollments (admin-only, paginated)",
        description=(
            "Admin-only paginated list of CourseEnrollment records, optionally filtered by "
            "course_id, course_ids, username, or email."
        ),
        parameters=[
            _query_param("course_id", "Filter to enrollments for this course."),
            _query_param("course_ids", "Comma-separated list of course IDs."),
            _query_param("username", "Comma-separated list of usernames."),
            _query_param("email", "Comma-separated list of emails."),
            _PAGE_QUERY_PARAM,
            _PAGE_SIZE_QUERY_PARAM,
        ],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentsApiListSerializer(many=True),
                description="Paginated list of course enrollments.",
            ),
            400: _RESP_BAD_REQUEST,
            401: _RESP_UNAUTHENTICATED,
            403: _RESP_FORBIDDEN,
        },
    ),
)
@can_disable_rate_limit
class CourseEnrollmentsApiListView(DeveloperErrorViewMixin, ListAPIView):
    """
    **Use Cases**

        Get a list of all course enrollments, optionally filtered by a course ID or list of usernames.

    **Example Requests**

        GET /api/enrollment/v1/enrollments

        GET /api/enrollment/v1/enrollments?course_id={course_id}

        GET /api/enrollment/v1/enrollments?course_ids={course_id},{course_id},{course_id}

        GET /api/enrollment/v1/enrollments?username={username},{username},{username}

        GET /api/enrollment/v1/enrollments?course_id={course_id}&username={username}

        GET /api/enrollment/v1/enrollments?email={email},{email}

    **Query Parameters for GET**

        * course_id: Filters the result to course enrollments for the course corresponding to the
          given course ID. The value must be URL encoded. Optional.

        * course_ids: List of comma-separated course IDs. Filters the result to course enrollments
          for the courses corresponding to the given course IDs. Course IDs could be course run IDs
          or course IDs. The value must be URL encoded. Optional.

        * username: List of comma-separated usernames. Filters the result to the course enrollments
          of the given users. Optional.

        * email: List of comma-separated emails. Filters the result to the course enrollments
          of the given users. Optional.

        * page_size: Number of results to return per page. Default 100, max 100. Optional.

        * page: Page number to retrieve. Default is 1. Optional.

    **Response Values**

        If the request for information about the course enrollments is successful, an HTTP 200 "OK" response
        is returned.

        The HTTP 200 response has the following values.

        * count: Total number of course enrollments matching the request.

        * num_pages: Total number of pages.

        * current_page: The current page number.

        * start: The 0-based index of the first item on this page.

        * next: The URL to the next page of results, or null if this is the
          last page.

        * previous: The URL to the previous page of results, or null if this
          is the first page.

        * results: A list of the course enrollments matching the request.

            * created: Date and time when the course enrollment was created.

            * mode: Mode for the course enrollment.

            * is_active: Whether the course enrollment is active or not.

            * user: Username of the user in the course enrollment.

            * course_id: Course ID of the course in the course enrollment.

        If the user is not logged in, a 401 error is returned.

        If the user is not global staff, a 403 error is returned.

        If the specified course_id is not valid or any of the specified usernames
        are not valid, a 400 error is returned.

        If the specified course_id does not correspond to a valid course or if all the specified
        usernames do not correspond to valid users, an HTTP 200 "OK" response is returned with an
        empty 'results' field.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.IsAdminUser,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseEnrollmentsApiListSerializer
    pagination_class = CourseEnrollmentsApiListPagination

    def get_queryset(self):
        """
        Get all the course enrollments for the given course_id and/or given list of usernames.
        """
        form = CourseEnrollmentsApiListForm(self.request.query_params)

        if not form.is_valid():
            raise ValidationError(form.errors)

        queryset = CourseEnrollment.objects.all().select_related("user", "course")
        course_id = form.cleaned_data.get("course_id")
        course_ids = form.cleaned_data.get("course_ids")
        usernames = form.cleaned_data.get("username")
        emails = form.cleaned_data.get("email")

        if course_id:
            queryset = queryset.filter(course__id=course_id)
        if course_ids:
            # Handles the case if parent course ID is sent rather than course run ID
            query = Q()
            for cid in course_ids:
                query |= Q(course__id__icontains=cid)
            queryset = queryset.filter(query)
        if usernames:
            queryset = queryset.filter(user__username__in=usernames)
        if emails:
            queryset = queryset.filter(user__email__in=emails)
        return queryset


# DEPRECATED (ADR 0028): Use EnrollmentViewSet.allowed action instead. Will be removed after one named release.
class EnrollmentAllowedView(APIView):
    """
    A view that allows the retrieval and creation of enrollment allowed for a given user email and course id.
    """

    permission_classes = (permissions.IsAdminUser,)
    throttle_classes = (EnrollmentUserThrottle,)
    serializer_class = CourseEnrollmentAllowedSerializer

    @extend_schema(
        operation_id="enrollment_v1_enrollment_allowed_list_deprecated",
        summary="List allowed enrollments by email (deprecated)",
        description=(
            "Deprecated. Use GET /api/enrollment/v1/enrollment/enrollment_allowed/ "
            "(EnrollmentViewSet.allowed action) instead. Admin-only."
        ),
        parameters=[_query_param("email", "Email to query. Defaults to the requester's email if omitted.")],
        responses={
            200: OpenApiResponse(
                response=CourseEnrollmentAllowedSerializer(many=True),
                description="Allowed enrollments retrieved successfully.",
            ),
            403: _RESP_FORBIDDEN,
        },
        deprecated=True,
    )
    def get(self, request):
        """
        Returns the enrollments allowed for a given user email.

        **Example Requests**

        GET /api/enrollment/v1/enrollment_allowed?email=user@example.com

        **Parameters**

        - `email` (optional, string, _query_params_) - defaults to the calling user if not provided.

        **Responses**
        - 200: Success.
        - 403: Forbidden, you need to be staff.
        """
        user_email = request.query_params.get("email")
        if not user_email:
            user_email = request.user.email

        enrollments_allowed = CourseEnrollmentAllowed.objects.filter(email=user_email)
        serializer = self.serializer_class(enrollments_allowed, many=True)
        return Response(status=status.HTTP_200_OK, data=serializer.data)

    @extend_schema(
        operation_id="enrollment_v1_enrollment_allowed_create_deprecated",
        summary="Create an allowed enrollment (deprecated)",
        description=(
            "Deprecated. Use POST /api/enrollment/v1/enrollment/enrollment_allowed/ "
            "(EnrollmentViewSet.allowed action) instead. Admin-only."
        ),
        request=OpenApiRequest(request=CourseEnrollmentAllowedSerializer),
        responses={
            201: OpenApiResponse(
                response=CourseEnrollmentAllowedSerializer,
                description="Allowed enrollment created.",
            ),
            400: _RESP_BAD_REQUEST,
            403: _RESP_FORBIDDEN,
            409: OpenApiResponse(description="Allowed enrollment already exists for this email/course."),
        },
        deprecated=True,
    )
    def post(self, request):
        """
        Creates an enrollment allowed for a given user email and course id.

        **Example Request**

        POST /api/enrollment/v1/enrollment_allowed/

        Note: The URL for this request must finish with /

        Example request data:
        ```
        {
            "email": "user@example.com",
            "course_id": "course-v1:edX+DemoX+Demo_Course",
            "auto_enroll": true
        }
        ```

        **Parameters**

        - `email` (**required**, string, _body_)

        - `course_id` (**required**, string, _body_)

        - `auto_enroll` (optional, bool: default=false, _body_)

        **Responses**
        - 400: Bad request, missing data.
        - 403: Forbidden, you need to be staff.
        - 409: Conflict, enrollment allowed already exists.
        """
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(status=status.HTTP_400_BAD_REQUEST, data=serializer.errors)

        try:
            enrollment_allowed = serializer.save()
        except IntegrityError:
            return Response(
                status=status.HTTP_409_CONFLICT,
                data={
                    "message": (
                        f"An enrollment allowed with email {serializer.validated_data.get('email')} "
                        f"and course {serializer.validated_data.get('course_id')} already exists."
                    )
                },
            )

        return Response(status=status.HTTP_201_CREATED, data=self.serializer_class(enrollment_allowed).data)

    @extend_schema(
        operation_id="enrollment_v1_enrollment_allowed_destroy_deprecated",
        summary="Delete an allowed enrollment (deprecated)",
        description=(
            "Deprecated. Use DELETE /api/enrollment/v1/enrollment/enrollment_allowed/ "
            "(EnrollmentViewSet.allowed action) instead. Admin-only."
        ),
        request=OpenApiRequest(request=CourseEnrollmentAllowedSerializer),
        responses={
            204: OpenApiResponse(description="Allowed enrollment deleted."),
            400: _RESP_BAD_REQUEST,
            403: _RESP_FORBIDDEN,
            404: OpenApiResponse(description="Allowed enrollment not found for the given email/course."),
        },
        deprecated=True,
    )
    def delete(self, request):
        """
        Deletes an enrollment allowed for a given user email and course id.

        **Example Request**

        DELETE /api/enrollment/v1/enrollment_allowed/

        Note: The URL for this request must finish with /

        Example request data:
        ```
        {
            "email": "user@example.com",
            "course_id": "course-v1:edX+DemoX+Demo_Course"
        }
        ```

        **Parameters**

        - `email` (**required**, string, _body_)

        - `course_id` (**required**, string, _body_)

        **Responses**
        - 204: Enrollment allowed deleted.
        - 400: Bad request, missing data.
        - 403: Forbidden, you need to be staff.
        - 404: Not found, the course enrollment allowed doesn't exists.
        """
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            return Response(status=status.HTTP_400_BAD_REQUEST, data=serializer.errors)

        email = serializer.validated_data.get("email")
        course_id = serializer.validated_data.get("course_id")

        try:
            CourseEnrollmentAllowed.objects.get(email=email, course_id=course_id).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ObjectDoesNotExist:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
                data={"message": f"An enrollment allowed with email {email} and course {course_id} doesn't exists."},
            )
