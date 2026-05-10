"""
Tests for the XblockViewSet (ADR 0028 ViewSet migration).

Three test classes — all use APITestCase (no MongoDB required):
  - TestXblockViewSetDetailPermissions  — auth/permission gates on the detail URL
  - TestXblockViewSetCreatePermissions  — auth/permission gates on the create (list) URL
  - TestXblockViewSetActions            — functional routing tests (handle_xblock is mocked)
"""
import json
from unittest.mock import patch

from django.http import JsonResponse
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.tests.factories import UserFactory

# A valid v2 block key; course key encoded as dede+aba+weagi
TEST_LOCATOR = "block-v1:dede+aba+weagi+type@problem+block@ba6327f840da49289fb27a9243913478"
MOCK_HANDLE_XBLOCK = "cms.djangoapps.contentstore.rest_api.v0.views.xblock.handle_xblock"
_MOCK_RESPONSE = JsonResponse({"locator": TEST_LOCATOR, "courseKey": "course-v1:dede+aba+weagi"})


class TestXblockViewSetDetailPermissions(APITestCase):
    """
    ADR 0028 – permission boundary tests for XblockViewSet detail actions.

    course_id is derived from TEST_LOCATOR in XblockViewSet.initial(); no MongoDB needed.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse(
            'cms.djangoapps.contentstore:v0:xblock-detail',
            kwargs={'usage_key_string': TEST_LOCATOR},
        )

    def test_unauthenticated_get_gets_401(self):
        """Unauthenticated GET must be rejected before reaching handle_xblock."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_delete_gets_401(self):
        """Unauthenticated DELETE must be rejected before reaching handle_xblock."""
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_course_author_get_gets_403(self):
        """Authenticated user without course-author access must get 403."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_course_author_delete_gets_403(self):
        """Authenticated user without course-author access must get 403 on DELETE."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestXblockViewSetCreatePermissions(APITestCase):
    """
    ADR 0028 – permission boundary tests for XblockViewSet create action.

    course_id is derived from parent_locator in XblockViewSet.initial(); no MongoDB needed.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse('cms.djangoapps.contentstore:v0:xblock-list')
        # A valid parent_locator so initial() can derive course_id for permission check
        self.create_payload = json.dumps({
            'parent_locator': TEST_LOCATOR,
            'category': 'html',
        })

    def test_unauthenticated_post_gets_401(self):
        """Unauthenticated POST must be rejected with 401."""
        response = self.client.post(self.url, self.create_payload, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_course_author_post_gets_403(self):
        """Authenticated user without course-author access must get 403."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.post(self.url, self.create_payload, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestXblockViewSetActions(APITestCase):
    """
    ADR 0028 – functional routing tests for XblockViewSet actions.

    handle_xblock is mocked so no module store or xblock infrastructure is needed.
    GlobalStaff (is_staff=True) satisfies HasCourseAuthorAccess without a real course.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.staff_user = UserFactory.create(is_staff=True)
        self.client.force_authenticate(user=self.staff_user)
        self.detail_url = reverse(
            'cms.djangoapps.contentstore:v0:xblock-detail',
            kwargs={'usage_key_string': TEST_LOCATOR},
        )
        self.list_url = reverse('cms.djangoapps.contentstore:v0:xblock-list')

    @patch(MOCK_HANDLE_XBLOCK, return_value=_MOCK_RESPONSE)
    def test_retrieve_calls_handle_xblock(self, mock_handle):
        """GET detail URL calls handle_xblock with the usage_key_string."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_handle.assert_called_once()
        self.assertEqual(mock_handle.call_args[0][1], TEST_LOCATOR)

    @patch(MOCK_HANDLE_XBLOCK, return_value=_MOCK_RESPONSE)
    def test_update_calls_handle_xblock(self, mock_handle):
        """PUT detail URL calls handle_xblock with the usage_key_string."""
        payload = {'category': 'html', 'data': '<p>Updated</p>', 'id': TEST_LOCATOR}
        response = self.client.put(
            self.detail_url, json.dumps(payload), content_type='application/json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_handle.assert_called_once()
        self.assertEqual(mock_handle.call_args[0][1], TEST_LOCATOR)

    @patch(MOCK_HANDLE_XBLOCK, return_value=_MOCK_RESPONSE)
    def test_partial_update_calls_handle_xblock(self, mock_handle):
        """PATCH detail URL calls handle_xblock with the usage_key_string."""
        payload = {'category': 'html', 'data': '<p>Patched</p>', 'id': TEST_LOCATOR}
        response = self.client.patch(
            self.detail_url, json.dumps(payload), content_type='application/json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_handle.assert_called_once()
        self.assertEqual(mock_handle.call_args[0][1], TEST_LOCATOR)

    @patch(MOCK_HANDLE_XBLOCK, return_value=_MOCK_RESPONSE)
    def test_destroy_calls_handle_xblock(self, mock_handle):
        """DELETE detail URL calls handle_xblock with the usage_key_string."""
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_handle.assert_called_once()
        self.assertEqual(mock_handle.call_args[0][1], TEST_LOCATOR)

    @patch(MOCK_HANDLE_XBLOCK, return_value=_MOCK_RESPONSE)
    def test_create_calls_handle_xblock(self, mock_handle):
        """POST list URL calls handle_xblock with usage_key_string=None."""
        payload = {'parent_locator': TEST_LOCATOR, 'category': 'html'}
        response = self.client.post(
            self.list_url, json.dumps(payload), content_type='application/json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_handle.assert_called_once()
        self.assertEqual(mock_handle.call_args[0][1], None)

# ---------------------------------------------------------------------------
# ADR 0029 – Standardize Error Responses
# ---------------------------------------------------------------------------

_REQUIRED_ERROR_FIELDS = ("type", "title", "status", "detail", "instance")


class TestXblockViewSetErrorShape(APITestCase):
    """
    ADR 0029 – error response shape regression tests for XblockViewSet.

    Verifies that auth/permission error responses conform to the standardized
    JSON envelope after removing DeveloperErrorViewMixin.
    """

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.detail_url = reverse(
            'cms.djangoapps.contentstore:v0:xblock-detail',
            kwargs={'usage_key_string': TEST_LOCATOR},
        )
        self.list_url = reverse('cms.djangoapps.contentstore:v0:xblock-list')

    def test_unauthenticated_get_returns_standardized_401(self):
        """Unauthenticated GET must return 401 with the ADR 0029 envelope."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        for field in _REQUIRED_ERROR_FIELDS:
            self.assertIn(field, response.data, f"ADR 0029: missing error field '{field}'")

    def test_unauthenticated_401_type_uri(self):
        """The ``type`` field for 401 must be the ADR 0029 authn URI."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data.get('type'), 'https://docs.openedx.org/errors/authn')

    def test_non_author_get_returns_standardized_403(self):
        """Authenticated non-author GET must return 403 with the ADR 0029 envelope."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        for field in _REQUIRED_ERROR_FIELDS:
            self.assertIn(field, response.data, f"ADR 0029: missing error field '{field}'")

    def test_non_author_403_type_uri(self):
        """The ``type`` field for 403 must be the ADR 0029 authz URI."""
        non_author = UserFactory.create()
        self.client.force_authenticate(user=non_author)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data.get('type'), 'https://docs.openedx.org/errors/authz')

    def test_error_body_has_no_developer_message(self):
        """Error responses must NOT contain the old DeveloperErrorViewMixin 'developer_message' key."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn('developer_message', response.data)
        self.assertNotIn('error_code', response.data)

    def test_instance_field_is_request_path(self):
        """The ``instance`` field must equal the request path."""
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data.get('instance'), self.detail_url)
