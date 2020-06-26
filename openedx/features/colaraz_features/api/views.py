"""
Views for colaraz API.
"""
import json
import logging
import requests
from six.moves.urllib.parse import urlencode

from django.conf import settings

from rest_framework import viewsets, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.features.colaraz_features.api import serializers

LOGGER = logging.getLogger(__name__)

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
    authentication_classes = (SessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    API_METHODS = {
        'fetch': 'METHOD_FETCH_NOTIFICATIONS',
        'mark': 'METHOD_MARK_NOTIFICATIONS',
    }

    def get(self, request, api_method, format=None):
        """
        This method uses Colaraz's Notifications API to fetch and mark notifications
        and returns data sent by API in json format.

        The response we get while fetching notifications is of the following pattern:
        {
            "status": 0,
            "result": [
                {
                    "from_guid": "<FROM GUID>",
                    "description": "<SOME HTML CONTENT>",
                    "time": "<RELATIVE TIME>",
                    "image": "<IMAGE SRC>",
                    "read": <STATUS REGARDING ITS READ/UNREAD STATE>
                }
            ]
        }
        """
        elgg_id = request.user.colaraz_profile.elgg_id
        api_details = getattr(settings, 'COLARAZ_NOTIFICATIONS', {})
        is_enabled = api_details.get('ENABLE', False)
        method_key = self.API_METHODS.get(api_method)

        if is_enabled and elgg_id and method_key:
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
            LOGGER.error('Notifications API is not enabled or is not configured properly')
        return Response(
            {'message': 'Notifications API is not enabled or is not configured properly'},
            status=status.HTTP_400_BAD_REQUEST
        )


class JobAlertsHandlerApiView(APIView):
    """
    APIView to fetch job alerts and mark them as read.
    """
    authentication_classes = (SessionAuthentication,)
    permission_classes = (IsAuthenticated,)

    API_ENDPOINTS = {
        'fetch': 'FETCH_URL',
        'mark': 'MARK_URL'
    }

    def get(self, request, api_method, format=None):
        """
        This method uses Colaraz's Job Alerts API to fetch and mark alerts
        and returns data sent by API in json format.

        The response we get while fetching job alerts is of the following pattern:
        {
            "d": [
                {
                    "__type": "<SOME TYPE IDENTIFIER>",
                    "Heading": "<JOB HEADING>",
                    "Message": "<JOB MESSAGE>",
                    "RelativeTime": "<RELATIVE TIME OF ALERT>",
                    "NotificationType": <NOTIFICATION TYPE IDENTIFIER>
                }
            ]
        }
        """
        email_id = request.user.email
        api_details = getattr(settings, 'COLARAZ_JOB_ALERTS', {})
        is_enabled = api_details.get('ENABLE', False)
        method_key = self.API_ENDPOINTS.get(api_method)
        api_url = api_details.get(method_key)

        if is_enabled and email_id and api_url:
            resp = requests.post(api_url, json={'email': email_id})
            json_data = json.loads(resp.content)
            if resp.status_code == status.HTTP_200_OK:
                return Response(json_data, status=status.HTTP_200_OK)
            else:
                LOGGER.error('Job Alerts API returned following error message: "{}" with status: "{}"'.format(
                        json_data.get('Message'),
                        resp.status_code,
                    )
                )
        else:
            LOGGER.error('Job Alerts API is not enabled or is not configured properly')

        return Response(
            {'message': 'Job Alerts API is not enabled or is not configured properly'},
            status=status.HTTP_400_BAD_REQUEST
        )
