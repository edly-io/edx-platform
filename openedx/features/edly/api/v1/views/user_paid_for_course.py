from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.json_request import JsonResponse


class UserPaidForCourseViewSet(viewsets.ViewSet):
    """
    **Use Case**

        Get the status of a user's paid status for a given course.

    **Example Request**

        GET /api/v1/user_paid_for_course/{course_id}

    **GET Parameters**

        * pk: The course id of the course to retrieve the user's paid status for.

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * has_user_paid: True if the user has paid for the course, False otherwise.
    """
    permission_classes = [IsAuthenticated]

    def retrieve(self, request, pk=None):
        """Get the status of a user's paid status for a given course."""
        try:
            course_key = CourseKey.from_string(pk)
            course_enrollment = CourseEnrollment.get_enrollment(request.user, course_key)
        except InvalidKeyError:
            return JsonResponse({'has_user_paid': False}, status=406)

        paid_status = course_enrollment.get_order_attribute_value('order_number')
        return JsonResponse({'has_user_paid': bool(paid_status)}, status=200)
