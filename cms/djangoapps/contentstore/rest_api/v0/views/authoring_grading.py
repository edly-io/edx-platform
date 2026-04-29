""" API Views for course grading settings """

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiRequest,
    OpenApiResponse,
)
from opaque_keys.edx.keys import CourseKey
from rest_framework import viewsets
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from rest_framework.permissions import IsAuthenticated

from cms.djangoapps.contentstore.views.permissions import HasStudioReadAccess
from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from openedx.core.djangoapps.credit.tasks import update_credit_course_requirements
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, verify_course_exists
from ..serializers import CourseGradingModelSerializer


# ADR 0028 – consolidated from AuthoringGradingView
class AuthoringGradingViewSet(DeveloperErrorViewMixin, viewsets.ViewSet):
    """
    ViewSet for getting and setting the grading settings for a course.

    Registered via DefaultRouter with basename ``authoring-grading``.
    Router-generated URL: PATCH /api/contentstore/v0/grading/{course_id}/
    """

    authentication_classes = (  # ADR 0026
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, HasStudioReadAccess)  # ADR 0026
    serializer_class = CourseGradingModelSerializer

    # DefaultRouter lookup: matches course-v1:org+course+run (+ or / separators)
    lookup_field = 'course_id'
    lookup_value_regex = r'[^/+]+(?:/|\+)[^/+]+(?:/|\+)[^/?]+'

    def get_serializer(self, *args, **kwargs):
        """Return a serializer instance using the configured serializer_class."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Update a course's grading settings",
        description="Partially update the grading settings for the specified course.",
        request=OpenApiRequest(request=CourseGradingModelSerializer),
        parameters=[
            OpenApiParameter(
                name="course_id",
                description="Course ID",
                required=True,
                type=str,
                location=OpenApiParameter.PATH,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CourseGradingModelSerializer,
                description="Grading settings updated successfully.",
            ),
            401: OpenApiResponse(description="The requester is not authenticated."),
            403: OpenApiResponse(description="The requester cannot access the specified course."),
            404: OpenApiResponse(description="The requested course does not exist."),
        },
    )
    @verify_course_exists()
    def partial_update(self, request: Request, course_id: str):
        """
        Update a course's grading settings.

        **Example Request**

            PATCH /api/contentstore/v0/grading/{course_id}/

        **PATCH Parameters**

        The data sent for a patch request should follow this object.
        Here is an example request data that updates ``course_grading``:

        ```json
        {
            "graders": [
                {
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "",
                    "weight": 100,
                    "id": 0
                }
            ],
            "grade_cutoffs": {
                "A": 0.75,
                "B": 0.63,
                "C": 0.57,
                "D": 0.5
            },
            "grace_period": {
                "hours": 12,
                "minutes": 0
            },
            "minimum_grade_credit": 0.7,
            "is_credit_course": true
        }
        ```

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned.
        """
        course_key = CourseKey.from_string(course_id)

        if 'minimum_grade_credit' in request.data:
            update_credit_course_requirements.delay(str(course_key))

        updated_data = CourseGradingModel.update_from_json(course_key, request.data, request.user)
        serializer = self.get_serializer(updated_data)
        return Response(serializer.data)


# DEPRECATED (ADR 0028): Use AuthoringGradingViewSet instead.
# Will be removed after one named release. Use PATCH grading/{course_id}/ via the router URL.
class AuthoringGradingView(DeveloperErrorViewMixin, APIView):
    """
    View for getting and setting the advanced settings for a course.
    """
    authentication_classes = (  # ADR 0026
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, HasStudioReadAccess)  # ADR 0026
    serializer_class = CourseGradingModelSerializer

    @extend_schema(
        summary="Update a course's grading settings (deprecated)",
        description=(
            "Deprecated POST alias for updating course grading settings. "
            "Use PATCH /api/contentstore/v0/grading/{course_id}/ instead."
        ),
        request=OpenApiRequest(request=CourseGradingModelSerializer),
        parameters=[
            OpenApiParameter(
                name="course_id",
                description="Course ID",
                required=True,
                type=str,
                location=OpenApiParameter.PATH,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CourseGradingModelSerializer,
                description="Grading settings updated successfully.",
            ),
            401: OpenApiResponse(description="The requester is not authenticated."),
            403: OpenApiResponse(description="The requester cannot access the specified course."),
            404: OpenApiResponse(description="The requested course does not exist."),
        },
        deprecated=True,
    )
    @verify_course_exists()
    def post(self, request: Request, course_id: str):
        """
        Update a course's grading.

        **Example Request**

            POST /api/contentstore/v0/course_grading/{course_id}

        **POST Parameters**

        The data sent for a post request should follow next object.
        Here is an example request data that updates the ``course_grading``

        ```json
        {
            "graders": [
                {
                    "type": "Homework",
                    "min_count": 1,
                    "drop_count": 0,
                    "short_label": "",
                    "weight": 100,
                    "id": 0
                }
            ],
            "grade_cutoffs": {
                "A": 0.75,
                "B": 0.63,
                "C": 0.57,
                "D": 0.5
            },
            "grace_period": {
                "hours": 12,
                "minutes": 0
            },
            "minimum_grade_credit": 0.7,
            "is_credit_course": true
        }
        ```

        **Response Values**

        If the request is successful, an HTTP 200 "OK" response is returned,
        """
        course_key = CourseKey.from_string(course_id)

        if 'minimum_grade_credit' in request.data:
            update_credit_course_requirements.delay(str(course_key))

        updated_data = CourseGradingModel.update_from_json(course_key, request.data, request.user)
        serializer = self.serializer_class(updated_data)
        return Response(serializer.data)
