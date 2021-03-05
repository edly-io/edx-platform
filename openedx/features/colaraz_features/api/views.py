"""
Views for colaraz API.
"""
import json
import logging
import requests
from six import text_type
from six.moves.urllib.parse import urlencode

from bleach import clean
from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from opaque_keys.edx.keys import CourseKey, UsageKey
from rest_framework import viewsets, status
from rest_framework_oauth.authentication import OAuth2Authentication
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers as rest_serializers

from edxmako.shortcuts import marketing_link, render_to_response, render_to_string
from lms.djangoapps.courseware.access import has_access, has_ccx_coach_role
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect, Redirect
from lms.djangoapps.courseware.module_render import (get_module, get_module_by_usage_id, get_module_for_descriptor,
                                                     _invoke_xblock_handler)
from openedx.features.colaraz_features.api import serializers
from openedx.features.colaraz_features.api.validators import TokenBasedAuthentication
from openedx.features.course_experience.utils import get_course_outline_block_tree
from opaque_keys import InvalidKeyError
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

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
        try:
            serializer = self.serializer_class(data=request.data)
            if serializer.is_valid(raise_exception=True):
                data = serializer.create(serializer.validated_data)
        except (rest_serializers.ValidationError, ValueError, NameError) as ex:
            return Response(
                {'error': str(ex)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except:
            return Response(
                {'error': 'Request data is unappropriate'},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {'success': data},
            status=status.HTTP_201_CREATED
        )


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

            try:
                resp = requests.get(api_url, params=query_params)
            except:
                return Response(
                    {'message': 'Notification API is unreachable at the moment. Please try again later.'},
                    status=status.HTTP_404_NOT_FOUND
                )
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
            try:
                resp = requests.post(api_url, json={'email': email_id})
            except:
                return Response(
                    {'message': 'Job Alerts API is unreachable at the moment. Please try again later.'},
                    status=status.HTTP_404_NOT_FOUND
                )
            if resp.status_code == status.HTTP_200_OK:
                json_data = json.loads(resp.content)
                return Response(json_data, status=status.HTTP_200_OK)
            else:
                LOGGER.error('Job Alerts API returned status: "{}"'.format(
                        resp.status_code,
                    )
                )
        else:
            LOGGER.error('Job Alerts API is not enabled or is not configured properly')

        return Response(
            {'message': 'Job Alerts API is not enabled or is not configured properly'},
            status=status.HTTP_400_BAD_REQUEST
        )


class CourseOutlineView(APIView):
    authentication_classes = (OAuth2Authentication, )
    permission_classes = (IsAuthenticated,)

    def get(self, request, course_id):
        """
        api to get course outline in sequence
        :param request:
        :param course_id:
        :return: dictionary of course outline tree structure
        """
        user = request.user
        try:
            course_tree = get_course_outline_block_tree(request, course_id, user)
        except ItemNotFoundError:
            # this exception is raised in few cases like if invalid course_id is passed
            return Response(data='Course not found', status=status.HTTP_404_NOT_FOUND)
        if course_tree is None:
            return Response(data='Course not found', status=status.HTTP_404_NOT_FOUND)
        return Response(course_tree, status=status.HTTP_200_OK)


class CourseXBlockApi(APIView):
    authentication_classes = (TokenBasedAuthentication,)
    permission_classes = (IsAuthenticated,)

    def get(self, request, usage_key_string):
        """
        Returns an HttpResponse with HTML content for the xBlock with the given usage_key.
        The returned HTML is a chromeless rendering of the xBlock (excluding content of the containing courseware).
        """
        usage_key = UsageKey.from_string(usage_key_string)

        usage_key = usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))
        course_key = usage_key.course_key

        requested_view = request.GET.get('view', 'student_view')
        if requested_view != 'student_view':
            return HttpResponseBadRequest(
                "Rendering of the xblock view '{}' is not supported.".format(clean(requested_view, strip=True))
            )

        with modulestore().bulk_operations(course_key):
            # verify the user has access to the course, including enrollment check
            try:
                course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=True)
            except CourseAccessRedirect:
                raise Http404("Course not found.")

            # get the block, which verifies whether the user has access to the block.
            block, _ = get_module_by_usage_id(
                request, text_type(course_key), text_type(usage_key), disable_staff_debug_info=True, course=course
            )

            student_view_context = request.GET.dict()
            student_view_context['show_bookmark_button'] = False

            enable_completion_on_view_service = False
            completion_service = block.runtime.service(block, 'completion')
            if completion_service and completion_service.completion_tracking_enabled():
                if completion_service.blocks_to_mark_complete_on_view({block}):
                    enable_completion_on_view_service = True
                    student_view_context['wrap_xblock_data'] = {
                        'mark-completed-on-view-after-delay': completion_service.get_complete_on_view_delay_ms()
                    }

            context = {
                'fragment': block.render('student_view', context=student_view_context),
                'course': course,
                'disable_accordion': True,
                'allow_iframing': True,
                'disable_header': True,
                'disable_footer': True,
                'disable_window_wrap': False,
                'mobile_view': True,
                'enable_completion_on_view_service': enable_completion_on_view_service,
                'staff_access': bool(has_access(request.user, 'staff', course)),
                'xqa_server': settings.FEATURES.get('XQA_SERVER', 'http://your_xqa_server.com'),
            }
            return render_to_response('courseware/courseware-chromeless.html', context)


class HandleXblockCallback(APIView):
    authentication_classes = (TokenBasedAuthentication,)
    permission_classes = (IsAuthenticated,)

    def post(self, request, course_id, usage_id, handler, suffix=None):
        """
        Arguments:
            request (Request): Django request.
            course_id (str): Course containing the block
            usage_id (str)
            handler (str)
            suffix (str)

        Raises:
            Http404: If the course is not found in the modulestore.
        """
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            raise Http404('{} is not a valid course key'.format(course_id))

        with modulestore().bulk_operations(course_key):
            try:
                course = modulestore().get_course(course_key)
            except ItemNotFoundError:
                raise Http404('{} does not exist in the modulestore'.format(course_id))

            return _invoke_xblock_handler(request, course_id, usage_id, handler, suffix, course=course)
