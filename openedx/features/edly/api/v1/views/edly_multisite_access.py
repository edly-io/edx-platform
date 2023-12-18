"""
Views for user sites API
"""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from lms.djangoapps.mobile_api.decorators import mobile_view
from openedx.features.edly.models import EdlyMultiSiteAccess
from openedx.features.edly.api.serializers import EdlyMultisiteAccessSerializer

@mobile_view()
class EdlyMultisiteAccessViewSet(viewsets.ViewSet):
    """
    **Use Case**

        Get information about the organizations associated with a user.

    **Example Request**

        GET /api/v1/courses/multisite_access/

    **Response Values**

        If the request is successful, the request returns an HTTP 200 "OK" response.

        The HTTP 200 response has the following values.
    """
    permission_classes = (IsAuthenticated,)

    def list(self, request, *args, **kwargs):
        """
        Get information about the organizations associated with a user.
        """
        serializer = EdlyMultisiteAccessSerializer(
            EdlyMultiSiteAccess.objects.filter(user=request.user), many=True
        )
        return Response(serializer.data)
