"""
Public rest API endpoints for the CMS API.
"""
import logging
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from django.views.decorators.csrf import csrf_exempt

from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes
from common.djangoapps.util.json_request import expect_json_in_class_view

from cms.djangoapps.contentstore.api import course_author_access_required
from cms.djangoapps.contentstore.xblock_storage_handlers import view_handlers

from ..serializers import XblockSerializer
from .utils import validate_request_with_serializer


log = logging.getLogger(__name__)
handle_xblock = view_handlers.handle_xblock


@view_auth_classes()
class XblockView(DeveloperErrorViewMixin, RetrieveUpdateDestroyAPIView):
    """
    Public rest API endpoints for the CMS API.
    course_key: required argument, needed to authorize course authors.
    usage_key_string (optional):
    xblock identifier, for example in the form of "block-v1:<course id>+type@<type>+block@<block id>"

    ADR 0025 compliance notes:
    - ``serializer_class`` is declared and used for input validation via
      ``@validate_request_with_serializer`` on mutating methods.
    - Response formatting is delegated to ``handle_xblock()`` which produces
      its own JSON shape; wrapping its output in ``XblockSerializer`` requires
      a deeper refactor and is tracked as a follow-up task.
    """
    serializer_class = XblockSerializer

    # pylint: disable=arguments-differ
    @course_author_access_required
    @expect_json_in_class_view
    def retrieve(self, request, course_key, usage_key_string=None):
        return handle_xblock(request, usage_key_string)

    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def update(self, request, course_key, usage_key_string=None):
        return handle_xblock(request, usage_key_string)

    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def partial_update(self, request, course_key, usage_key_string=None):
        return handle_xblock(request, usage_key_string)

    @course_author_access_required
    @expect_json_in_class_view
    def destroy(self, request, course_key, usage_key_string=None):
        return handle_xblock(request, usage_key_string)


@view_auth_classes()
class XblockCreateView(DeveloperErrorViewMixin, CreateAPIView):
    """
    Public rest API endpoints for the CMS API.
    course_key: required argument, needed to authorize course authors.
    usage_key_string (optional):
    xblock identifier, for example in the form of "block-v1:<course id>+type@<type>+block@<block id>"

    ADR 0025 compliance notes:
    - ``serializer_class`` is declared and used for input validation via
      ``@validate_request_with_serializer``.
    - Response formatting is delegated to ``handle_xblock()``; full response
      serialization is tracked as a follow-up task.
    """
    serializer_class = XblockSerializer

    # pylint: disable=arguments-differ
    @csrf_exempt
    @course_author_access_required
    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request, course_key, usage_key_string=None):
        return handle_xblock(request, usage_key_string)
