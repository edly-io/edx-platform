"""HomePageCoursesViewV2 APIView for getting content available to the logged in user."""

from collections import OrderedDict

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiResponse,
)
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.paginators import DefaultPagination

from cms.djangoapps.contentstore.utils import get_course_context_v2
from cms.djangoapps.contentstore.rest_api.v2.serializers import CourseHomeTabSerializerV2


def _query_param(name: str, description: str) -> OpenApiParameter:
    """Build a string-typed, optional query parameter (preserves api-doc-tools behavior)."""
    return OpenApiParameter(
        name=name,
        description=description,
        required=False,
        type=str,
        location=OpenApiParameter.QUERY,
    )


_HOME_COURSES_QUERY_PARAMETERS = [
    _query_param("org", "Query param to filter by course org"),
    _query_param("search", "Query param to filter by course name, org, or number"),
    _query_param("order", "Query param to order by course name, org, or number"),
    _query_param("active_only", "Query param to filter by active courses only"),
    _query_param("archived_only", "Query param to filter by archived courses only"),
    _query_param("page", "Query param to paginate the courses"),
    _query_param("page_size", "Query param to set page size"),
]
_UNAUTHENTICATED_RESPONSE = OpenApiResponse(description="The requester is not authenticated.")


class HomePageCoursesPaginator(DefaultPagination):
    """
    ADR 0032 – standard pagination for the Studio home courses list (v2).

    Extends DefaultPagination with the full 7-field response envelope:
    count, num_pages, current_page, start, next, previous, results.
    Handles Python ``filter`` objects returned by get_course_context_v2.
    """
    page_size_query_param = 'page_size'

    def paginate_queryset(self, queryset, request, view=None):
        """
        Paginate a queryset if required, either returning a page object,
        or `None` if pagination is not configured for this view.

        This method is a modified version of the original `paginate_queryset` method
        from the `PageNumberPagination` class. The original method was modified to
        handle the case where the `queryset` is a `filter` object.
        """
        if isinstance(queryset, filter):
            queryset = list(queryset)

        return super().paginate_queryset(queryset, request, view)


class HomeCoursesViewSetV2(viewsets.ViewSet):
    """
    ViewSet for course listing (v2).  Registered via DefaultRouter (basename ``home-courses``).

    Router-generated URLs:
      GET  /api/contentstore/v2/home/courses/  → list
    """
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseHomeTabSerializerV2

    def get_serializer(self, *args, **kwargs):
        """Instantiate and return the configured serializer class."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="List courses for the Studio home page (paginated)",
        description=(
            "Returns a paginated list of all courses available to the logged-in user, "
            "with optional filtering and ordering."
        ),
        parameters=_HOME_COURSES_QUERY_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=CourseHomeTabSerializerV2,
                description="Paginated course list retrieved successfully.",
            ),
            401: _UNAUTHENTICATED_RESPONSE,
        },
    )
    def list(self, request: Request):
        """
        Get a paginated list of all courses available to the logged-in user.

        **Example Request**

            GET /api/contentstore/v2/home/courses/
            GET /api/contentstore/v2/home/courses/?org=edX
            GET /api/contentstore/v2/home/courses/?search=E2E
            GET /api/contentstore/v2/home/courses/?order=-org
            GET /api/contentstore/v2/home/courses/?active_only=true
            GET /api/contentstore/v2/home/courses/?archived_only=true
            GET /api/contentstore/v2/home/courses/?page=2
            GET /api/contentstore/v2/home/courses/?page_size=20

        **Response Values**

        If the request is successful, an HTTP 200 \"OK\" response is returned.

        The HTTP 200 response is paginated and contains ``count``, ``num_pages``,
        ``next``, ``previous`` and ``results`` keys.  ``results`` contains the
        serialized course data.
        """
        courses, in_process_course_actions = get_course_context_v2(request)
        paginator = HomePageCoursesPaginator()
        courses_page = paginator.paginate_queryset(courses, request, view=self)
        serializer = self.get_serializer({
            'courses': courses_page,
            'in_process_course_actions': in_process_course_actions,
        })
        return paginator.get_paginated_response(serializer.data)


class HomePageCoursesViewV2(APIView):
    """View for getting all courses available to the logged in user."""
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated,)
    serializer_class = CourseHomeTabSerializerV2

    @extend_schema(
        operation_id="v2_home_courses_retrieve_deprecated",
        summary="List courses for the Studio home page (deprecated)",
        description=(
            "Deprecated. Use GET /api/contentstore/v2/home/courses/ instead."
        ),
        parameters=_HOME_COURSES_QUERY_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=CourseHomeTabSerializerV2,
                description="Paginated course list retrieved successfully.",
            ),
            401: _UNAUTHENTICATED_RESPONSE,
        },
        deprecated=True,
    )
    def get(self, request: Request):
        """
        Get an object containing all courses.

        **Example Request**

            GET /api/contentstore/v2/home/courses
            GET /api/contentstore/v2/home/courses?org=edX
            GET /api/contentstore/v2/home/courses?search=E2E
            GET /api/contentstore/v2/home/courses?order=-org
            GET /api/contentstore/v2/home/courses?active_only=true
            GET /api/contentstore/v2/home/courses?archived_only=true
            GET /api/contentstore/v2/home/courses?page=2
            GET /api/contentstore/v2/home/courses?page_size=20

        **Pagination Parameters**

            - ``page`` (int): Page number to retrieve. Default is 1.
            - ``page_size`` (int): Items per page. Default is 10, max is 100.

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response contains the ADR 0032 standard pagination envelope.

        **Response Envelope (ADR 0032)**

            - ``count`` (int): Total number of courses matching the filters.
            - ``num_pages`` (int): Total number of pages.
            - ``current_page`` (int): The current page number.
            - ``start`` (int): The 0-based index of the first course on this page.
            - ``next`` (str|null): URL for the next page, or null if this is the last page.
            - ``previous`` (str|null): URL for the previous page, or null if this is the first page.
            - ``results`` (dict): Course data for the current page.

        **Example Response**

        ```json
        {
            "count": 2,
            "num_pages": 1,
            "current_page": 1,
            "start": 0,
            "next": null,
            "previous": null,
            "results": {
                "courses": [
                     {
                        "course_key": "course-v1:edX+E2E-101+course",
                        "display_name": "E2E Test Course",
                        "lms_link": "//localhost:18000/courses/course-v1:edX+E2E-101+course",
                        "cms_link": "//localhost:18010/course/course-v1:edX+E2E-101+course",
                        "number": "E2E-101",
                        "org": "edX",
                        "rerun_link": "/course_rerun/course-v1:edX+E2E-101+course",
                        "run": "course",
                        "url": "/course/course-v1:edX+E2E-101+course",
                        "is_active": true
                    }
                ],
                "in_process_course_actions": []
            }
        }
        ```
        """
        courses, in_process_course_actions = get_course_context_v2(request)
        paginator = HomePageCoursesPaginator()
        courses_page = paginator.paginate_queryset(
            courses,
            self.request,
            view=self
        )
        serializer = self.serializer_class({
            'courses': courses_page,
            'in_process_course_actions': in_process_course_actions,
        })
        return paginator.get_paginated_response(serializer.data)
