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
