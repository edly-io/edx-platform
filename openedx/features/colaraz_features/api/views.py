"""
Views for colaraz API.
"""
from rest_framework import viewsets, status
from rest_framework.response import Response

from openedx.features.colaraz_features.api import serializers


class SiteOrgViewSet(viewsets.ViewSet):
    """
    View set to enable creation of site, organization and theme via REST API.
    """
    serializer_class = serializers.SiteOrgSerializer

    def create(self, request):
        """
        Perform creation operation for site, organization, site theme and site configuration.
        """
        serializer = self.serializer_class(data=request.POST)

        if serializer.is_valid(raise_exception=True):
            serializer.create(serializer.validated_data)

        return Response(serializer.validated_data, status=status.HTTP_201_CREATED)
