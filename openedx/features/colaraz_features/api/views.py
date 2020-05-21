"""
Views for colaraz API.
"""
import json
import logging
import requests
from six.moves.urllib.parse import urlencode

from django.conf import settings

from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.features.colaraz_features.api import serializers

LOGGER = logging.getLogger(__name__)
API_METHODS = {
    'fetch': 'METHOD_FETCH_NOTIFICATIONS',
    'mark': 'METHOD_MARK_NOTIFICATIONS',
}

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


class NotificationHandlerApiView(APIView):
    """
    APIView to fetch notifications and mark them as read.
    """

    def get(self, request, api_method, format=None):
        """
        This method uses Colaraz's Notifications API to fetch and mark notifications
        and returns data sent by API in json format.
        """
        elgg_id = request.user.colaraz_profile.elgg_id
        api_details = getattr(settings, 'COLARAZ_NOTIFICATIONS')
        method_key = API_METHODS.get(api_method, None)

        if api_details and elgg_id and method_key:
            api_url = api_details.get('API_URL')
            query_params = urlencode({
                'api_key': api_details.get('API_KEY'),
                'guid': elgg_id,
                'method': api_details.get(method_key),
            })

            resp = requests.get(api_url, params=query_params)
            if resp.status_code == status.HTTP_200_OK:
                json_data = json.loads(resp.content)
                if json_data.get('status') == 0:
                    return Response(json_data, status=status.HTTP_200_OK)
                else:
                    LOGGER.error('Notifications API gave error: {}'.format(json_data.get('message')))
                    return Response(json_data, status=status.HTTP_400_BAD_REQUEST)
            else:
                LOGGER.error('Notifications API returned {} status'.format(resp.status_code))
        else:
            LOGGER.error('Notifications API parameters are not complete or configured properly')
        return Response(
            {'message': 'Notifications API is not configured properly'},
            status=status.HTTP_400_BAD_REQUEST
        )
