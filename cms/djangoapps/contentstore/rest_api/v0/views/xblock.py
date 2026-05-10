"""
Public rest API endpoints for the CMS API.
"""
import json
import logging
from rest_framework import viewsets
from rest_framework.generics import RetrieveUpdateDestroyAPIView, CreateAPIView
from django.views.decorators.csrf import csrf_exempt

from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from rest_framework.permissions import IsAuthenticated
from common.djangoapps.util.json_request import expect_json_in_class_view
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey

from cms.djangoapps.contentstore.views.permissions import HasCourseAuthorAccess
from cms.djangoapps.contentstore.xblock_storage_handlers import view_handlers

from ..serializers import XblockSerializer
from .utils import validate_request_with_serializer


log = logging.getLogger(__name__)
handle_xblock = view_handlers.handle_xblock


# ADR 0028 – consolidated from XblockView and XblockCreateView
class XblockViewSet(viewsets.ViewSet):
    """
    ViewSet for xblock CRUD operations.

    Registered via DefaultRouter with basename ``xblock``.
    Router-generated URLs:
      POST   /api/contentstore/v0/xblock/                          → create
      GET    /api/contentstore/v0/xblock/{usage_key_string}/       → retrieve
      PUT    /api/contentstore/v0/xblock/{usage_key_string}/       → update
      PATCH  /api/contentstore/v0/xblock/{usage_key_string}/       → partial_update
      DELETE /api/contentstore/v0/xblock/{usage_key_string}/       → destroy

    course_id is not included in the router URL. It is derived in initial() from:
      - usage_key_string (detail routes): the course key is embedded in the block key
      - parent_locator in request.data (create route)
    This ensures HasCourseAuthorAccess can still perform its course-level authorization.

    ADR 0025 compliance notes:
    - ``serializer_class`` is declared and used for input validation via
      ``@validate_request_with_serializer`` on mutating methods.
    - Response formatting is delegated to ``handle_xblock()`` which produces
      its own JSON shape; wrapping its output in ``XblockSerializer`` requires
      a deeper refactor and is tracked as a follow-up task.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, HasCourseAuthorAccess)  # ADR 0026
    serializer_class = XblockSerializer

    # Matches both i4x:// legacy keys and block-v1: v2 keys
    lookup_field = 'usage_key_string'
    lookup_value_regex = r'(?:i4x://?[^/]+/[^/]+/[^/]+/[^@]+(?:@[^/]+)?)|(?:[^/]+)'

    def get_serializer(self, *args, **kwargs):
        """Return a serializer instance using the configured serializer_class."""
        return self.serializer_class(*args, **kwargs)

    def initial(self, request, *args, **kwargs):
        """
        Inject ``course_id`` into self.kwargs before permission checks run.

        HasCourseAuthorAccess expects ``view.kwargs['course_id']``. Router-generated
        URLs omit course_id, so we derive it here:
          - Detail routes: extracted from ``usage_key_string`` (the course key is
            always encoded in the block key, e.g. block-v1:org+course+run+type@...).
          - Create route: extracted from ``parent_locator`` in the request body.
        """
        if 'course_id' not in self.kwargs:
            usage_key_string = self.kwargs.get('usage_key_string')
            if usage_key_string:
                try:
                    self.kwargs['course_id'] = str(
                        UsageKey.from_string(usage_key_string).course_key
                    )
                except InvalidKeyError:
                    pass
            else:
                # Create path: derive from parent_locator in the request body.
                # Access request._request.body (Django's raw bytes) rather than
                # request.data so that Django caches the body content before DRF
                # reads the stream.  If we used request.data here, the underlying
                # WSGI stream would be consumed and @expect_json_in_class_view
                # would later raise RawPostDataException when it tried to read
                # request.body.
                try:
                    raw = request._request.body
                    data = json.loads(raw) if raw else {}
                    parent_locator = data.get('parent_locator')
                    if parent_locator:
                        self.kwargs['course_id'] = str(
                            UsageKey.from_string(parent_locator).course_key
                        )
                except (InvalidKeyError, AttributeError, ValueError):
                    pass
        super().initial(request, *args, **kwargs)

    # pylint: disable=arguments-differ
    @expect_json_in_class_view
    def retrieve(self, request, usage_key_string=None, **kwargs):
        return handle_xblock(request, usage_key_string)

    @csrf_exempt
    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request, **kwargs):
        return handle_xblock(request, None)

    @expect_json_in_class_view
    @validate_request_with_serializer
    def update(self, request, usage_key_string=None, **kwargs):
        return handle_xblock(request, usage_key_string)

    @expect_json_in_class_view
    @validate_request_with_serializer
    def partial_update(self, request, usage_key_string=None, **kwargs):
        return handle_xblock(request, usage_key_string)

    @expect_json_in_class_view
    def destroy(self, request, usage_key_string=None, **kwargs):
        return handle_xblock(request, usage_key_string)


# DEPRECATED (ADR 0028): Use XblockViewSet instead.
# Will be removed after one named release. Use GET/PUT/PATCH/DELETE xblock/{usage_key_string}/ instead.
class XblockView(RetrieveUpdateDestroyAPIView):
    """
    Public rest API endpoints for the CMS API.
    course_id: required argument, needed to authorize course authors.
    usage_key_string (optional):
    xblock identifier, for example in the form of "block-v1:<course id>+type@<type>+block@<block id>"

    ADR 0025 compliance notes:
    - ``serializer_class`` is declared and used for input validation via
      ``@validate_request_with_serializer`` on mutating methods.
    - Response formatting is delegated to ``handle_xblock()`` which produces
      its own JSON shape; wrapping its output in ``XblockSerializer`` requires
      a deeper refactor and is tracked as a follow-up task.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, HasCourseAuthorAccess)
    serializer_class = XblockSerializer

    # pylint: disable=arguments-differ
    @expect_json_in_class_view
    def retrieve(self, request, course_id, usage_key_string=None):
        return handle_xblock(request, usage_key_string)

    @expect_json_in_class_view
    @validate_request_with_serializer
    def update(self, request, course_id, usage_key_string=None):
        return handle_xblock(request, usage_key_string)

    @expect_json_in_class_view
    @validate_request_with_serializer
    def partial_update(self, request, course_id, usage_key_string=None):
        return handle_xblock(request, usage_key_string)

    @expect_json_in_class_view
    def destroy(self, request, course_id, usage_key_string=None):
        return handle_xblock(request, usage_key_string)


# DEPRECATED (ADR 0028): Use XblockViewSet instead.
# Will be removed after one named release. Use POST xblock/ instead.
class XblockCreateView(CreateAPIView):
    """
    Public rest API endpoints for the CMS API.
    course_id: required argument, needed to authorize course authors.
    usage_key_string (optional):
    xblock identifier, for example in the form of "block-v1:<course id>+type@<type>+block@<block id>"

    ADR 0025 compliance notes:
    - ``serializer_class`` is declared and used for input validation via
      ``@validate_request_with_serializer``.
    - Response formatting is delegated to ``handle_xblock()``; full response
      serialization is tracked as a follow-up task.
    """
    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, HasCourseAuthorAccess)  # ADR 0026
    serializer_class = XblockSerializer

    # pylint: disable=arguments-differ
    @csrf_exempt
    @expect_json_in_class_view
    @validate_request_with_serializer
    def create(self, request, course_id, usage_key_string=None):
        return handle_xblock(request, usage_key_string)
