from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.catalog.utils import get_programs


class EdlyProgramEnrollmentCountViewSet(viewsets.ViewSet):
    """
    **Use Case**

        Get the number of enrollments for a program.

    **Example Request**

        GET /api/v1/programs/enrollment_count/{program_uuid}

    **GET Parameters**

        * program_uuid: The UUID of the program.

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.

        * enrolled_users_count: The number of enrollments for the program. 
    """
    permission_classes = (IsAuthenticated,)

    def list(self, request, *args, **kwargs):
        program_uuid = kwargs.get('program_uuid')
        program = get_programs(uuid=program_uuid)

        if not program:
            return Response({'detail': 'Program not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Get the course_run key for all the courses included in the program
        courses = program.get('courses', [])
        course_run_ids = [
            course_run['key']
            for course in courses
            for course_run in course.get('course_runs', [])
        ]

        enrolled_users_count = CourseEnrollment.objects.filter(
            course_id__in=course_run_ids).values('user').distinct().count()

        response_data = {
            'enrolled_users_count': enrolled_users_count,
        }

        return Response(response_data, status=status.HTTP_200_OK)
