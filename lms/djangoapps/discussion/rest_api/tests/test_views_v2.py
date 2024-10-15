"""
Tests for Discussion API views

This module contains tests for the Discussion API views. These tests are
replicated from 'lms/djangoapps/discussion/rest_api/tests/test_views.py'
and are adapted to use the forum v2 native APIs instead of the v1 HTTP calls.
"""

import json
import random
from datetime import datetime
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse

import ddt
import httpretty
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey
from pytz import UTC
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.test import APIClient, APITestCase

from lms.djangoapps.discussion.toggles import ENABLE_DISCUSSIONS_MFE
from lms.djangoapps.discussion.rest_api.utils import get_usernames_from_search_string
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import (
    CourseFactory,
    BlockFactory,
    check_mongo_calls,
)

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import (
    get_retired_username_by_username,
    CourseEnrollment,
)
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    GlobalStaff,
)
from common.djangoapps.student.tests.factories import (
    AdminFactory,
    CourseEnrollmentFactory,
    SuperuserFactory,
    UserFactory,
)
from common.djangoapps.util.testing import PatchMediaTypeMixin, UrlResetMixin
from common.test.utils import disable_signal
from lms.djangoapps.discussion.django_comment_client.tests.utils import (
    ForumsEnableMixin,
    config_course_discussions,
    topic_name_to_id,
)
from lms.djangoapps.discussion.rest_api import api
from lms.djangoapps.discussion.rest_api.tests.utils_v2 import (
    CommentsServiceMockMixin,
    ProfileImageTestMixin,
    make_minimal_cs_comment,
    make_minimal_cs_thread,
    make_paginated_api_response,
    parsed_body,
)
from openedx.core.djangoapps.course_groups.tests.helpers import config_course_cohorts
from openedx.core.djangoapps.discussions.config.waffle import (
    ENABLE_NEW_STRUCTURE_DISCUSSIONS,
)
from openedx.core.djangoapps.discussions.models import (
    DiscussionsConfiguration,
    DiscussionTopicLink,
    Provider,
)
from openedx.core.djangoapps.discussions.tasks import (
    update_discussions_settings_from_course_task,
)
from openedx.core.djangoapps.django_comment_common.models import (
    CourseDiscussionSettings,
    Role,
)
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import (
    AccessTokenFactory,
    ApplicationFactory,
)
from openedx.core.djangoapps.user_api.accounts.image_helpers import (
    get_profile_image_storage,
)
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    UserRetirementStatus,
)


class DiscussionAPIViewTestMixin(
    ForumsEnableMixin, CommentsServiceMockMixin, UrlResetMixin
):
    """
    Mixin for common code in tests of Discussion API views. This includes
    creation of common structures (e.g. a course, user, and enrollment), logging
    in the test client, utility functions, and a test case for unauthenticated
    requests. Subclasses must set self.url in their setUp methods.
    """

    client_class = APIClient

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        self.maxDiff = None  # pylint: disable=invalid-name
        self.course = CourseFactory.create(
            org="x",
            course="y",
            run="z",
            start=datetime.now(UTC),
            discussion_topics={"Test Topic": {"id": "test_topic"}},
        )
        self.password = "Password1234"
        self.user = UserFactory.create(password=self.password)
        # Ensure that parental controls don't apply to this user
        self.user.profile.year_of_birth = 1970
        self.user.profile.save()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.client.login(username=self.user.username, password=self.password)
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_update_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_thread"
        ).start()
        self.mock_get_parent_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_parent_comment"
        ).start()
        self.mock_update_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_comment"
        ).start()
        self.mock_create_parent_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_parent_comment"
        ).start()
        self.mock_create_child_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_child_comment"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def assert_response_correct(self, response, expected_status, expected_content):
        """
        Assert that the response has the given status code and parsed content
        """
        assert response.status_code == expected_status
        parsed_content = json.loads(response.content.decode("utf-8"))
        assert parsed_content == expected_content

    def register_thread(self, overrides=None):
        """
        Create cs_thread with minimal fields and register response
        """
        cs_thread = make_minimal_cs_thread(
            {
                "id": "test_thread",
                "course_id": str(self.course.id),
                "commentable_id": "test_topic",
                "username": self.user.username,
                "user_id": str(self.user.id),
                "thread_type": "discussion",
                "title": "Test Title",
                "body": "Test body",
            }
        )
        cs_thread.update(overrides or {})
        self.register_get_thread_response(cs_thread)
        self.register_put_thread_response(cs_thread)

    def register_comment(self, overrides=None):
        """
        Create cs_comment with minimal fields and register response
        """
        cs_comment = make_minimal_cs_comment(
            {
                "id": "test_comment",
                "course_id": str(self.course.id),
                "thread_id": "test_thread",
                "username": self.user.username,
                "user_id": str(self.user.id),
                "body": "Original body",
            }
        )
        cs_comment.update(overrides or {})
        self.register_get_comment_response(cs_comment)
        self.register_put_comment_response(cs_comment)
        self.register_post_comment_response(cs_comment, thread_id="test_thread")

    def test_not_authenticated(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assert_response_correct(
            response,
            401,
            {"developer_message": "Authentication credentials were not provided."},
        )

    def test_inactive(self):
        self.user.is_active = False
        self.test_basic()


@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class UploadFileViewTest(
    ForumsEnableMixin, CommentsServiceMockMixin, UrlResetMixin, ModuleStoreTestCase
):
    """
    Tests for UploadFileView.
    """

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()
        self.valid_file = {
            "uploaded_file": SimpleUploadedFile(
                "test.jpg",
                b"test content",
                content_type="image/jpeg",
            ),
        }
        self.user = UserFactory.create(password=self.TEST_PASSWORD)
        self.course = CourseFactory.create(
            org="a", course="b", run="c", start=datetime.now(UTC)
        )
        self.url = reverse("upload_file", kwargs={"course_id": str(self.course.id)})

        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def user_login(self):
        """
        Authenticates the test client with the example user.
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

    def enroll_user_in_course(self):
        """
        Makes the example user enrolled to the course.
        """
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

    def assert_upload_success(self, response):
        """
        Asserts that the upload response was successful and returned the
        expected contents.
        """
        assert response.status_code == status.HTTP_200_OK
        assert response.content_type == "application/json"
        response_data = json.loads(response.content)
        assert "location" in response_data

    def test_file_upload_by_unauthenticated_user(self):
        """
        Should fail if an unauthenticated user tries to upload a file.
        """
        response = self.client.post(self.url, self.valid_file)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_file_upload_by_unauthorized_user(self):
        """
        Should fail if the user is not either staff or a student
        enrolled in the course.
        """
        self.user_login()
        response = self.client.post(self.url, self.valid_file)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_file_upload_by_enrolled_user(self):
        """
        Should succeed when a valid file is uploaded by an authenticated
        user who's enrolled in the course.
        """
        self.user_login()
        self.enroll_user_in_course()
        response = self.client.post(self.url, self.valid_file)
        self.assert_upload_success(response)

    def test_file_upload_by_global_staff(self):
        """
        Should succeed when a valid file is uploaded by a global staff
        member.
        """
        self.user_login()
        GlobalStaff().add_users(self.user)
        response = self.client.post(self.url, self.valid_file)
        self.assert_upload_success(response)

    def test_file_upload_by_instructor(self):
        """
        Should succeed when a valid file is uploaded by a course instructor.
        """
        self.user_login()
        CourseInstructorRole(course_key=self.course.id).add_users(self.user)
        response = self.client.post(self.url, self.valid_file)
        self.assert_upload_success(response)

    def test_file_upload_by_course_staff(self):
        """
        Should succeed when a valid file is uploaded by a course staff
        member.
        """
        self.user_login()
        CourseStaffRole(course_key=self.course.id).add_users(self.user)
        response = self.client.post(self.url, self.valid_file)
        self.assert_upload_success(response)

    def test_file_upload_with_thread_key(self):
        """
        Should contain the given thread_key in the uploaded file name.
        """
        self.user_login()
        self.enroll_user_in_course()
        response = self.client.post(
            self.url,
            {
                **self.valid_file,
                "thread_key": "somethread",
            },
        )
        response_data = json.loads(response.content)
        assert "/somethread/" in response_data["location"]

    def test_file_upload_with_invalid_file(self):
        """
        Should fail if the uploaded file format is not allowed.
        """
        self.user_login()
        self.enroll_user_in_course()
        invalid_file = {
            "uploaded_file": SimpleUploadedFile(
                "test.txt",
                b"test content",
                content_type="text/plain",
            ),
        }
        response = self.client.post(self.url, invalid_file)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_file_upload_with_invalid_course_id(self):
        """
        Should fail if the course does not exist.
        """
        self.user_login()
        self.enroll_user_in_course()
        url = reverse("upload_file", kwargs={"course_id": "d/e/f"})
        response = self.client.post(url, self.valid_file)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_file_upload_with_no_data(self):
        """
        Should fail when the user sends a request missing an
        `uploaded_file` field.
        """
        self.user_login()
        self.enroll_user_in_course()
        response = self.client.post(self.url, data={})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetListByUserTest(
    ForumsEnableMixin,
    CommentsServiceMockMixin,
    UrlResetMixin,
    ModuleStoreTestCase,
):
    """
    Common test cases for views retrieving user-published content.
    """

    @mock.patch.dict(
        "django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True}
    )
    def setUp(self):
        super().setUp()

        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)

        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user_threads = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user_threads"
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.addCleanup(mock.patch.stopall)

        self.user = UserFactory.create(password=self.TEST_PASSWORD)
        self.register_get_user_response(self.user)

        self.other_user = UserFactory.create(password=self.TEST_PASSWORD)
        self.register_get_user_response(self.other_user)

        self.course = CourseFactory.create(
            org="a", course="b", run="c", start=datetime.now(UTC)
        )
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

        self.url = self.build_url(self.user.username, self.course.id)

    def register_mock_endpoints(self):
        """
        Register cs_comments_service mocks for sample threads and comments.
        """
        self.register_get_threads_response(
            threads=[
                make_minimal_cs_thread(
                    {
                        "id": f"test_thread_{index}",
                        "course_id": str(self.course.id),
                        "commentable_id": f"test_topic_{index}",
                        "username": self.user.username,
                        "user_id": str(self.user.id),
                        "thread_type": "discussion",
                        "title": f"Test Title #{index}",
                        "body": f"Test body #{index}",
                    }
                )
                for index in range(30)
            ],
            page=1,
            num_pages=1,
        )
        self.register_get_comments_response(
            comments=[
                make_minimal_cs_comment(
                    {
                        "id": f"test_comment_{index}",
                        "thread_id": "test_thread",
                        "user_id": str(self.user.id),
                        "username": self.user.username,
                        "created_at": "2015-05-11T00:00:00Z",
                        "updated_at": "2015-05-11T11:11:11Z",
                        "body": f"Test body #{index}",
                        "votes": {"up_count": 4},
                    }
                )
                for index in range(30)
            ],
            page=1,
            num_pages=1,
        )

    def build_url(self, username, course_id, **kwargs):
        """
        Builds an URL to access content from an user on a specific course.
        """
        base = reverse("comment-list")
        query = urlencode(
            {
                "username": username,
                "course_id": str(course_id),
                **kwargs,
            }
        )
        return f"{base}?{query}"

    def assert_successful_response(self, response):
        """
        Check that the response was successful and contains the expected fields.
        """
        assert response.status_code == status.HTTP_200_OK
        response_data = json.loads(response.content)
        assert "results" in response_data
        assert "pagination" in response_data

    def test_request_by_unauthenticated_user(self):
        """
        Unauthenticated users are not allowed to request users content.
        """
        self.register_mock_endpoints()
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_request_by_unauthorized_user(self):
        """
        Users are not allowed to request content from courses in which
        they're not either enrolled or staff members.
        """
        self.register_mock_endpoints()
        self.client.login(
            username=self.other_user.username, password=self.TEST_PASSWORD
        )
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert json.loads(response.content)["developer_message"] == "Course not found."

    def test_request_by_enrolled_user(self):
        """
        Users that are enrolled in a course are allowed to get users'
        comments in that course.
        """
        self.register_mock_endpoints()
        self.client.login(
            username=self.other_user.username, password=self.TEST_PASSWORD
        )
        CourseEnrollmentFactory.create(user=self.other_user, course_id=self.course.id)
        self.assert_successful_response(self.client.get(self.url))

    def test_request_by_global_staff(self):
        """
        Staff users are allowed to get any user's comments.
        """
        self.register_mock_endpoints()
        self.client.login(
            username=self.other_user.username, password=self.TEST_PASSWORD
        )
        GlobalStaff().add_users(self.other_user)
        self.assert_successful_response(self.client.get(self.url))

    @ddt.data(CourseStaffRole, CourseInstructorRole)
    def test_request_by_course_staff(self, role):
        """
        Course staff users are allowed to get an user's comments in that
        course.
        """
        self.register_mock_endpoints()
        self.client.login(
            username=self.other_user.username, password=self.TEST_PASSWORD
        )
        role(course_key=self.course.id).add_users(self.other_user)
        self.assert_successful_response(self.client.get(self.url))

    def test_request_with_non_existent_user(self):
        """
        Requests for users that don't exist result in a 404 response.
        """
        self.register_mock_endpoints()
        self.client.login(
            username=self.other_user.username, password=self.TEST_PASSWORD
        )
        GlobalStaff().add_users(self.other_user)
        url = self.build_url("non_existent", self.course.id)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_request_with_non_existent_course(self):
        """
        Requests for courses that don't exist result in a 404 response.
        """
        self.register_mock_endpoints()
        self.client.login(
            username=self.other_user.username, password=self.TEST_PASSWORD
        )
        GlobalStaff().add_users(self.other_user)
        url = self.build_url(self.user.username, "course-v1:x+y+z")
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_request_with_invalid_course_id(self):
        """
        Requests with invalid course ID should fail form validation.
        """
        self.register_mock_endpoints()
        self.client.login(
            username=self.other_user.username, password=self.TEST_PASSWORD
        )
        GlobalStaff().add_users(self.other_user)
        url = self.build_url(self.user.username, "an invalid course")
        response = self.client.get(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        parsed_response = json.loads(response.content)
        assert (
            parsed_response["field_errors"]["course_id"]["developer_message"]
            == "'an invalid course' is not a valid course id"
        )

    def test_request_with_empty_results_page(self):
        """
        Requests for pages that exceed the available number of pages
        result in a 404 response.
        """
        self.register_get_threads_response(threads=[], page=1, num_pages=1)
        self.register_get_comments_response(comments=[], page=1, num_pages=1)

        self.client.login(
            username=self.other_user.username, password=self.TEST_PASSWORD
        )
        GlobalStaff().add_users(self.other_user)
        url = self.build_url(self.user.username, self.course.id, page=2)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@override_settings(
    DISCUSSION_MODERATION_EDIT_REASON_CODES={"test-edit-reason": "Test Edit Reason"}
)
@override_settings(
    DISCUSSION_MODERATION_CLOSE_REASON_CODES={"test-close-reason": "Test Close Reason"}
)
class CourseViewTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for CourseView"""

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "discussion_course", kwargs={"course_id": str(self.course.id)}
        )
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_404(self):
        response = self.client.get(
            reverse("course_topics", kwargs={"course_id": "non/existent/course"})
        )
        self.assert_response_correct(
            response, 404, {"developer_message": "Course not found."}
        )

    def test_basic(self):
        response = self.client.get(self.url)
        self.assert_response_correct(
            response,
            200,
            {
                "id": str(self.course.id),
                "is_posting_enabled": True,
                "blackouts": [],
                "thread_list_url": "http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz",
                "following_thread_list_url": (
                    "http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz&following=True"
                ),
                "topics_url": "http://testserver/api/discussion/v1/course_topics/course-v1:x+y+z",
                "enable_in_context": True,
                "group_at_subsection": False,
                "provider": "legacy",
                "allow_anonymous": True,
                "allow_anonymous_to_peers": False,
                "has_moderation_privileges": False,
                "is_course_admin": False,
                "is_course_staff": False,
                "is_group_ta": False,
                "is_user_admin": False,
                "user_roles": ["Student"],
                "edit_reasons": [
                    {"code": "test-edit-reason", "label": "Test Edit Reason"}
                ],
                "post_close_reasons": [
                    {"code": "test-close-reason", "label": "Test Close Reason"}
                ],
                "show_discussions": True,
            },
        )


@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class RetireViewTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for CourseView"""

    def setUp(self):
        super().setUp()
        RetirementState.objects.create(state_name="PENDING", state_execution_order=1)
        self.retire_forums_state = RetirementState.objects.create(
            state_name="RETIRE_FORUMS", state_execution_order=11
        )

        self.retirement = UserRetirementStatus.create_retirement(self.user)
        self.retirement.current_state = self.retire_forums_state
        self.retirement.save()

        self.superuser = SuperuserFactory()
        self.superuser_client = APIClient()
        self.retired_username = get_retired_username_by_username(self.user.username)
        self.url = reverse("retire_discussion_user")

        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_retire_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.retire_user"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def assert_response_correct(self, response, expected_status, expected_content):
        """
        Assert that the response has the given status code and content
        """
        assert response.status_code == expected_status

        if expected_content:
            assert response.content.decode("utf-8") == expected_content

    def build_jwt_headers(self, user):
        """
        Helper function for creating headers for the JWT authentication.
        """
        token = create_jwt_for_user(user)
        headers = {"HTTP_AUTHORIZATION": "JWT " + token}
        return headers

    def perform_retirement(self):
        """
        Helper method to perform the retirement action and return the response.
        """
        self.register_get_user_retire_response(self.user)
        headers = self.build_jwt_headers(self.superuser)
        data = {"username": self.user.username}
        response = self.superuser_client.post(self.url, data, **headers)

        self.mock_retire_user.assert_called_once_with(
            user_id=str(self.user.id),
            retired_username=get_retired_username_by_username(self.user.username),
            course_id=None,
        )

        return response

    # @mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.retire_user')
    def test_basic(self):
        """
        Check successful retirement case
        """
        response = self.perform_retirement()
        self.assert_response_correct(response, 204, b"")

    # @mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.retire_user')
    def test_inactive(self):
        """
        Test retiring an inactive user
        """
        self.user.is_active = False
        response = self.perform_retirement()
        self.assert_response_correct(response, 204, b"")

    def test_downstream_forums_error(self):
        """
        Check that we bubble up errors from the comments service
        """
        self.mock_retire_user.side_effect = Exception("Server error")

        headers = self.build_jwt_headers(self.superuser)
        data = {"username": self.user.username}
        response = self.superuser_client.post(self.url, data, **headers)

        # Verify that the response contains the expected error status and message
        self.assert_response_correct(response, 500, '"Server error"')

    def test_nonexistent_user(self):
        """
        Check that we handle unknown users appropriately
        """
        nonexistent_username = "nonexistent user"
        self.retired_username = get_retired_username_by_username(nonexistent_username)
        data = {"username": nonexistent_username}
        headers = self.build_jwt_headers(self.superuser)
        response = self.superuser_client.post(self.url, data, **headers)
        self.assert_response_correct(response, 404, None)

    def test_not_authenticated(self):
        """
        Override the parent implementation of this, we JWT auth for this API
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass


@ddt.ddt
@httpretty.activate
@mock.patch(
    "django.conf.settings.USERNAME_REPLACEMENT_WORKER",
    "test_replace_username_service_worker",
)
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ReplaceUsernamesViewTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for ReplaceUsernamesView"""

    def setUp(self):
        super().setUp()
        self.worker = UserFactory()
        self.worker.username = "test_replace_username_service_worker"
        self.worker_client = APIClient()
        self.new_username = "test_username_replacement"
        self.url = reverse("replace_discussion_username")

        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_update_username = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.update_username"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def assert_response_correct(self, response, expected_status, expected_content):
        """
        Assert that the response has the given status code and content
        """
        assert response.status_code == expected_status

        if expected_content:
            assert str(response.content) == expected_content

    def build_jwt_headers(self, user):
        """
        Helper function for creating headers for the JWT authentication.
        """
        token = create_jwt_for_user(user)
        headers = {"HTTP_AUTHORIZATION": "JWT " + token}
        return headers

    def call_api(self, user, client, data):
        """Helper function to call API with data"""
        data = json.dumps(data)
        headers = self.build_jwt_headers(user)
        return client.post(self.url, data, content_type="application/json", **headers)

    @ddt.data([{}, {}], {}, [{"test_key": "test_value", "test_key_2": "test_value_2"}])
    def test_bad_schema(self, mapping_data):
        """Verify the endpoint rejects bad data schema"""
        data = {"username_mappings": mapping_data}
        response = self.call_api(self.worker, self.worker_client, data)
        assert response.status_code == 400

    def test_auth(self):
        """Verify the endpoint only works with the service worker"""
        data = {
            "username_mappings": [
                {"test_username_1": "test_new_username_1"},
                {"test_username_2": "test_new_username_2"},
            ]
        }

        # Test unauthenticated
        response = self.client.post(self.url, data)
        assert response.status_code == 403

        # Test non-service worker
        random_user = UserFactory()
        response = self.call_api(random_user, APIClient(), data)
        assert response.status_code == 403

        # Test service worker
        response = self.call_api(self.worker, self.worker_client, data)
        assert response.status_code == 200

    def test_basic(self):
        """Check successful replacement"""
        data = {
            "username_mappings": [
                {self.user.username: self.new_username},
            ]
        }
        expected_response = {
            "failed_replacements": [],
            "successful_replacements": data["username_mappings"],
        }
        self.register_get_username_replacement_response(self.user)
        response = self.call_api(self.worker, self.worker_client, data)
        assert response.status_code == 200
        assert response.data == expected_response

    def test_not_authenticated(self):
        """
        Override the parent implementation of this, we JWT auth for this API
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass


@ddt.ddt
@mock.patch("lms.djangoapps.discussion.rest_api.api._get_course", mock.Mock())
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, True)
class CourseTopicsViewV3Test(
    DiscussionAPIViewTestMixin, CommentsServiceMockMixin, ModuleStoreTestCase
):
    """
    Tests for CourseTopicsViewV3
    """

    def setUp(self) -> None:
        super().setUp()
        self.password = self.TEST_PASSWORD
        self.user = UserFactory.create(password=self.password)
        self.client.login(username=self.user.username, password=self.password)
        self.staff = AdminFactory.create()
        self.course = CourseFactory.create(
            start=datetime(2020, 1, 1),
            end=datetime(2028, 1, 1),
            enrollment_start=datetime(2020, 1, 1),
            enrollment_end=datetime(2028, 1, 1),
            discussion_topics={
                "Course Wide Topic": {
                    "id": "course-wide-topic",
                    "usage_key": None,
                }
            },
        )
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="chapter",
            display_name="Week 1",
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category="sequential",
            display_name="Lesson 1",
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.verticals = [
            BlockFactory.create(
                parent_location=self.sequential.location,
                category="vertical",
                display_name="vertical",
                start=datetime(2015, 4, 1, tzinfo=UTC),
            )
        ]
        course_key = self.course.id
        self.config = DiscussionsConfiguration.objects.create(
            context_key=course_key, provider_type=Provider.OPEN_EDX
        )
        topic_links = []
        update_discussions_settings_from_course_task(str(course_key))
        topic_id_query = DiscussionTopicLink.objects.filter(
            context_key=course_key
        ).values_list(
            "external_id",
            flat=True,
        )
        topic_ids = list(topic_id_query.order_by("ordering"))
        DiscussionTopicLink.objects.bulk_create(topic_links)
        self.topic_stats = {
            **{
                topic_id: dict(
                    discussion=random.randint(0, 10), question=random.randint(0, 10)
                )
                for topic_id in set(topic_ids)
            },
            topic_ids[0]: dict(discussion=0, question=0),
        }
        mock.patch(
            "lms.djangoapps.discussion.rest_api.api.get_course_commentable_counts",
            mock.Mock(return_value=self.topic_stats),
        ).start()
        self.url = reverse(
            "course_topics_v3", kwargs={"course_id": str(self.course.id)}
        )
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_basic(self):
        response = self.client.get(self.url)
        data = json.loads(response.content.decode())
        expected_non_courseware_keys = [
            "id",
            "usage_key",
            "name",
            "thread_counts",
            "enabled_in_context",
            "courseware",
        ]
        expected_courseware_keys = [
            "id",
            "block_id",
            "lms_web_url",
            "legacy_web_url",
            "student_view_url",
            "type",
            "display_name",
            "children",
            "courseware",
        ]
        assert response.status_code == 200
        assert len(data) == 2
        non_courseware_topic_keys = list(data[0].keys())
        assert non_courseware_topic_keys == expected_non_courseware_keys
        courseware_topic_keys = list(data[1].keys())
        assert courseware_topic_keys == expected_courseware_keys
        expected_courseware_keys.remove("courseware")
        sequential_keys = list(data[1]["children"][0].keys())
        assert sequential_keys == (expected_courseware_keys + ["thread_counts"])
        expected_non_courseware_keys.remove("courseware")
        vertical_keys = list(data[1]["children"][0]["children"][0].keys())
        assert vertical_keys == expected_non_courseware_keys


@ddt.ddt
@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetListTest(
    DiscussionAPIViewTestMixin, ModuleStoreTestCase, ProfileImageTestMixin
):
    """Tests for ThreadViewSet list"""

    def setUp(self):
        super().setUp()
        self.author = UserFactory.create()
        self.url = reverse("thread-list")
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_get_user_threads = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user_threads"
        ).start()
        self.mock_search_threads = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.search_threads"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def create_source_thread(self, overrides=None):
        """
        Create a sample source cs_thread
        """
        thread = make_minimal_cs_thread(
            {
                "id": "test_thread",
                "course_id": str(self.course.id),
                "commentable_id": "test_topic",
                "user_id": str(self.user.id),
                "username": self.user.username,
                "created_at": "2015-04-28T00:00:00Z",
                "updated_at": "2015-04-28T11:11:11Z",
                "title": "Test Title",
                "body": "Test body",
                "votes": {"up_count": 4},
                "comments_count": 5,
                "unread_comments_count": 3,
            }
        )

        thread.update(overrides or {})
        return thread

    def test_course_id_missing(self):
        response = self.client.get(self.url)
        self.assert_response_correct(
            response,
            400,
            {
                "field_errors": {
                    "course_id": {"developer_message": "This field is required."}
                }
            },
        )

    def test_404(self):
        response = self.client.get(self.url, {"course_id": "non/existent/course"})
        self.assert_response_correct(
            response, 404, {"developer_message": "Course not found."}
        )

    def test_basic(self):
        self.register_get_user_response(self.user, upvoted_ids=["test_thread"])
        source_threads = [
            self.create_source_thread(
                {"user_id": str(self.author.id), "username": self.author.username}
            )
        ]
        expected_threads = [
            self.expected_thread_data(
                {
                    "created_at": "2015-04-28T00:00:00Z",
                    "updated_at": "2015-04-28T11:11:11Z",
                    "vote_count": 4,
                    "comment_count": 6,
                    "can_delete": False,
                    "unread_comment_count": 3,
                    "voted": True,
                    "author": self.author.username,
                    "editable_fields": [
                        "abuse_flagged",
                        "copy_link",
                        "following",
                        "read",
                        "voted",
                    ],
                    "abuse_flagged_count": None,
                }
            )
        ]

        # Mock the response from get_user_threads
        self.mock_get_user_threads.return_value = {
            "collection": source_threads,
            "page": 1,
            "num_pages": 2,
            "thread_count": len(source_threads),
            "corrected_text": None,
        }

        response = self.client.get(
            self.url, {"course_id": str(self.course.id), "following": ""}
        )
        expected_response = make_paginated_api_response(
            results=expected_threads,
            count=1,
            num_pages=2,
            next_link="http://testserver/api/discussion/v1/threads/?course_id=course-v1%3Ax%2By%2Bz&following=&page=2",
            previous_link=None,
        )
        expected_response.update({"text_search_rewrite": None})
        self.assert_response_correct(response, 200, expected_response)

        # Verify the query parameters
        self.mock_get_user_threads.assert_called_once_with(
            user_id=str(self.user.id),
            course_id=str(self.course.id),
            sort_key="activity",
            page=1,
            per_page=10,
        )

    @ddt.data("unread", "unanswered", "unresponded")
    def test_view_query(self, query):
        threads = [make_minimal_cs_thread()]
        self.register_get_user_response(self.user)
        self.register_get_threads_response(
            threads, page=1, num_pages=1, overrides={"corrected_text": None}
        )

        self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "view": query,
            },
        )
        self.mock_get_user_threads.assert_called_once_with(
            user_id=str(self.user.id),
            course_id=str(self.course.id),
            sort_key="activity",
            page=1,
            per_page=10,
            **{query: "true"},
        )

    def test_pagination(self):
        self.register_get_user_response(self.user)
        self.register_get_threads_response(
            [], page=1, num_pages=1, overrides={"corrected_text": None}
        )
        response = self.client.get(
            self.url, {"course_id": str(self.course.id), "page": "18", "page_size": "4"}
        )

        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Page not found (No results on this page)."},
        )

        # Verify the query parameters
        self.mock_get_user_threads.assert_called_once_with(
            user_id=str(self.user.id),
            course_id=str(self.course.id),
            sort_key="activity",
            page=18,
            per_page=4,
        )

    def test_text_search(self):
        self.register_get_user_response(self.user)
        self.register_get_threads_search_response([], None, num_pages=0)
        response = self.client.get(
            self.url,
            {"course_id": str(self.course.id), "text_search": "test search string"},
        )

        expected_response = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_response.update({"text_search_rewrite": None})
        self.assert_response_correct(response, 200, expected_response)
        self.mock_search_threads.assert_called_once_with(
            user_id=str(self.user.id),
            course_id=str(self.course.id),
            sort_key="activity",
            page=1,
            per_page=10,
            text="test search string",
        )

    @ddt.data(True, "true", "1")
    def test_following_true(self, following):
        self.register_get_user_response(self.user)
        self.register_subscribed_threads_response(self.user, [], page=1, num_pages=0)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "following": following,
            },
        )
        expected_response = make_paginated_api_response(
            results=[], count=0, num_pages=0, next_link=None, previous_link=None
        )
        expected_response.update({"text_search_rewrite": None})
        self.assert_response_correct(response, 200, expected_response)

        self.mock_get_user_threads.assert_called_once_with(
            course_id=str(self.course.id),
            user_id=str(self.user.id),
            sort_key="activity",
            page=1,
            per_page=10,
            group_id=None,
            text="",
            author_id=None,
            flagged=None,
            thread_type="",
            count_flagged=None,
        )

    @ddt.data(False, "false", "0")
    def test_following_false(self, following):
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "following": following,
            },
        )
        self.assert_response_correct(
            response,
            400,
            {
                "field_errors": {
                    "following": {
                        "developer_message": "The value of the 'following' parameter must be true."
                    }
                }
            },
        )

    def test_following_error(self):
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "following": "invalid-boolean",
            },
        )
        self.assert_response_correct(
            response,
            400,
            {
                "field_errors": {
                    "following": {"developer_message": "Invalid Boolean Value."}
                }
            },
        )

    @ddt.data(
        ("last_activity_at", "activity"),
        ("comment_count", "comments"),
        ("vote_count", "votes"),
    )
    @ddt.unpack
    def test_order_by(self, http_query, cc_query):
        """
        Tests the order_by parameter

        Arguments:
            http_query (str): Query string sent in the http request
            cc_query (str): Query string used for the comments client service
        """
        threads = [make_minimal_cs_thread()]
        self.register_get_user_response(self.user)
        self.register_get_threads_response(threads, page=1, num_pages=1)
        self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "order_by": http_query,
            },
        )
        self.mock_get_user_threads.assert_called_once_with(
            user_id=str(self.user.id),
            course_id=str(self.course.id),
            sort_key=cc_query,
            page=1,
            per_page=10,
        )

    def test_order_direction(self):
        """
        Test order direction, of which "desc" is the only valid option.  The
        option actually just gets swallowed, so it doesn't affect the params.
        """
        threads = [make_minimal_cs_thread()]
        self.register_get_user_response(self.user)
        self.register_get_threads_response(threads, page=1, num_pages=1)
        self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "order_direction": "desc",
            },
        )
        self.mock_get_user_threads.assert_called_once_with(
            user_id=str(self.user.id),
            course_id=str(self.course.id),
            sort_key="activity",
            page=1,
            per_page=10,
        )

    def test_mutually_exclusive(self):
        """
        Tests GET thread_list api does not allow filtering on mutually exclusive parameters
        """
        self.register_get_user_response(self.user)
        self.mock_search_threads.side_effect = ValueError(
            "The following query parameters are mutually exclusive: topic_id, text_search, following"
        )
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "text_search": "test search string",
                "topic_id": "topic1, topic2",
            },
        )
        self.assert_response_correct(
            response,
            400,
            {
                "developer_message": "The following query parameters are mutually exclusive: topic_id, "
                "text_search, following"
            },
        )

    def test_profile_image_requested_field(self):
        """
        Tests thread has user profile image details if called in requested_fields
        """
        user_2 = UserFactory.create(password=self.password)
        # Ensure that parental controls don't apply to this user
        user_2.profile.year_of_birth = 1970
        user_2.profile.save()
        source_threads = [
            self.create_source_thread(),
            self.create_source_thread(
                {"user_id": str(user_2.id), "username": user_2.username}
            ),
        ]

        self.register_get_user_response(self.user, upvoted_ids=["test_thread"])
        self.register_get_threads_response(source_threads, page=1, num_pages=1)
        self.create_profile_image(self.user, get_profile_image_storage())
        self.create_profile_image(user_2, get_profile_image_storage())

        response = self.client.get(
            self.url,
            {"course_id": str(self.course.id), "requested_fields": "profile_image"},
        )
        assert response.status_code == 200
        response_threads = json.loads(response.content.decode("utf-8"))["results"]

        for response_thread in response_threads:
            expected_profile_data = self.get_expected_user_profile(
                response_thread["author"]
            )
            response_users = response_thread["users"]
            assert expected_profile_data == response_users[response_thread["author"]]

    def test_profile_image_requested_field_anonymous_user(self):
        """
        Tests profile_image in requested_fields for thread created with anonymous user
        """
        source_threads = [
            self.create_source_thread(
                {
                    "user_id": None,
                    "username": None,
                    "anonymous": True,
                    "anonymous_to_peers": True,
                }
            ),
        ]

        self.register_get_user_response(self.user, upvoted_ids=["test_thread"])
        self.register_get_threads_response(source_threads, page=1, num_pages=1)

        response = self.client.get(
            self.url,
            {"course_id": str(self.course.id), "requested_fields": "profile_image"},
        )
        assert response.status_code == 200
        response_thread = json.loads(response.content.decode("utf-8"))["results"][0]
        assert response_thread["author"] is None
        assert {} == response_thread["users"]


@httpretty.activate
@disable_signal(api, "thread_created")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetCreateTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for ThreadViewSet create"""

    def setUp(self):
        super().setUp()
        self.url = reverse("thread-list")
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_create_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_thread"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_basic(self):
        self.register_get_user_response(self.user)
        cs_thread = make_minimal_cs_thread(
            {
                "id": "test_thread",
                "username": self.user.username,
                "read": True,
            }
        )
        self.register_post_thread_response(cs_thread)
        request_data = {
            "course_id": str(self.course.id),
            "topic_id": "test_topic",
            "type": "discussion",
            "title": "Test Title",
            "raw_body": "# Test \n This is a very long body but will not be truncated for the preview.",
        }
        self.client.post(
            self.url, json.dumps(request_data), content_type="application/json"
        )
        self.mock_create_thread.assert_called_once_with(
            title="Test Title",
            body="# Test \n This is a very long body but will not be truncated for the preview.",
            course_id=str(self.course.id),
            user_id=str(self.user.id),
            anonymous=False,
            anonymous_to_peers=False,
            commentable_id="test_topic",
            thread_type="discussion",
            group_id=None,
            context=None,
        )

    def test_error(self):
        request_data = {
            "topic_id": "dummy",
            "type": "discussion",
            "title": "dummy",
            "raw_body": "dummy",
        }
        response = self.client.post(
            self.url, json.dumps(request_data), content_type="application/json"
        )
        expected_response_data = {
            "field_errors": {
                "course_id": {"developer_message": "This field is required."}
            }
        }
        assert response.status_code == 400
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == expected_response_data


@ddt.ddt
@httpretty.activate
@disable_signal(api, "thread_edited")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetPartialUpdateTest(
    DiscussionAPIViewTestMixin, ModuleStoreTestCase, PatchMediaTypeMixin
):
    """Tests for ThreadViewSet partial_update"""

    def setUp(self):
        self.unsupported_media_type = JSONParser.media_type
        super().setUp()
        self.url = reverse("thread-detail", kwargs={"thread_id": "test_thread"})
        from openedx.core.djangoapps.django_comment_common.comment_client.thread import (
            Thread,
        )

        self.existing_thread = Thread(
            **make_minimal_cs_thread(
                {
                    "id": "existing_thread",
                    "course_id": str(self.course.id),
                    "commentable_id": "original_topic",
                    "thread_type": "discussion",
                    "title": "Original Title",
                    "body": "Original body",
                    "user_id": str(self.user.id),
                    "username": self.user.username,
                    "read": "False",
                    "endorsed": "False",
                }
            )
        )
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_get_course_id_by_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread"
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_update_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_thread"
        ).start()
        self.mock_update_thread_flag = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.update_thread_flag"
        ).start()
        self.mock_update_thread_flag_in_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.comment.forum_api.update_thread_flag"
        ).start()
        self.mock_mark_thread_as_read = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.mark_thread_as_read"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_basic(self):
        self.register_get_user_response(self.user)
        self.register_thread(
            {
                "id": "existing_thread",  # Ensure the correct thread ID is used
                "title": "Edited Title",  # Ensure the correct title is used
                "topic_id": "edited_topic",  # Ensure the correct topic is used
                "thread_type": "question",  # Ensure the correct thread type is used
                "created_at": "Test Created Date",
                "updated_at": "Test Updated Date",
                "read": True,
                "resp_total": 2,
            }
        )
        request_data = {
            "raw_body": "Edited body",
            "topic_id": "edited_topic",  # Ensure the correct topic is used in the request
        }
        self.request_patch(request_data)
        self.mock_update_thread.assert_called_once_with(
            thread_id="existing_thread",  # Use the correct thread ID
            title="Edited Title",  # Use the correct title
            body="Edited body",
            course_id=str(self.course.id),
            anonymous=False,  # anonymous
            anonymous_to_peers=False,  # anonymous_to_peers
            closed=False,  # closed
            commentable_id="edited_topic",  # Use the correct topic
            user_id=str(self.user.id),
            editing_user_id=str(self.user.id),  # editing_user_id
            pinned=False,  # pinned
            thread_type="question",  # Use the correct thread type
            course_key=str(self.course.id),
        )

    def test_error(self):
        self.register_get_user_response(self.user)
        self.register_thread()
        request_data = {"title": ""}
        response = self.request_patch(request_data)
        expected_response_data = {
            "field_errors": {
                "title": {"developer_message": "This field may not be blank."}
            }
        }
        assert response.status_code == 400
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == expected_response_data

    @ddt.data(
        ("abuse_flagged", True),
        ("abuse_flagged", False),
    )
    @ddt.unpack
    def test_closed_thread(self, field, value):
        self.register_get_user_response(self.user)
        self.register_thread({"closed": True, "read": True})
        self.register_flag_response("thread", "test_thread")
        request_data = {field: value}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_thread_data(
            {
                "read": True,
                "closed": True,
                "abuse_flagged": value,
                "editable_fields": ["abuse_flagged", "copy_link", "read"],
                "comment_count": 1,
                "unread_comment_count": 0,
            }
        )

    @ddt.data(
        ("raw_body", "Edited body"),
        ("voted", True),
        ("following", True),
    )
    @ddt.unpack
    def test_closed_thread_error(self, field, value):
        self.register_get_user_response(self.user)
        self.register_thread({"closed": True})
        self.register_flag_response("thread", "test_thread")
        request_data = {field: value}
        response = self.request_patch(request_data)
        assert response.status_code == 400

    def test_patch_read_owner_user(self):
        self.register_get_user_response(self.user)
        self.register_thread({"resp_total": 2})
        self.register_read_response(self.user, "thread", "test_thread")
        request_data = {"read": True}

        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_thread_data(
            {
                "comment_count": 1,
                "read": True,
                "editable_fields": [
                    "abuse_flagged",
                    "anonymous",
                    "copy_link",
                    "following",
                    "raw_body",
                    "read",
                    "title",
                    "topic_id",
                    "type",
                ],
                "response_count": 2,
            }
        )
        self.mock_mark_thread_as_read.assert_called_once_with(
            str(self.user.id), "test_thread", course_id=str(self.course.id)
        )

    def test_patch_read_non_owner_user(self):
        self.register_get_user_response(self.user)
        thread_owner_user = UserFactory.create(password=self.password)
        CourseEnrollmentFactory.create(user=thread_owner_user, course_id=self.course.id)
        self.register_get_user_response(thread_owner_user)
        self.register_thread(
            {
                "username": thread_owner_user.username,
                "user_id": str(thread_owner_user.id),
                "resp_total": 2,
            }
        )
        self.register_read_response(self.user, "thread", "test_thread")

        request_data = {"read": True}
        self.request_patch(request_data)
        self.mock_mark_thread_as_read.assert_called_once_with(
            str(thread_owner_user.id), "test_thread", course_id=str(self.course.id)
        )


@httpretty.activate
@disable_signal(api, "thread_deleted")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetDeleteTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for ThreadViewSet delete"""

    def setUp(self):
        super().setUp()
        self.url = reverse("thread-detail", kwargs={"thread_id": "test_thread"})
        self.thread_id = "test_thread"
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_get_course_id_by_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread"
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_delete_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.delete_thread"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_basic(self):
        self.register_get_user_response(self.user)
        cs_thread = make_minimal_cs_thread(
            {
                "id": self.thread_id,
                "course_id": str(self.course.id),
                "username": self.user.username,
                "user_id": str(self.user.id),
            }
        )
        self.register_get_thread_response(cs_thread)
        self.register_delete_thread_response(self.thread_id)
        response = self.client.delete(self.url)
        assert response.status_code == 204
        assert response.content == b""
        self.mock_delete_thread.assert_called_once_with(
            thread_id=self.thread_id, course_id=str(self.course.id)
        )

    # def test_delete_nonexistent_thread(self):
    #     self.register_get_thread_error_response(self.thread_id, 404)
    #     response = self.client.delete(
    #         self.url,
    #         {"course_id": str(self.course.id)},
    #         "json",
    #     )
    #     assert response.status_code == 404
    #     self.mock_delete_thread.assert_called_once_with(
    #         thread_id=self.thread_id, course_id=str(self.course.id)
    #     )


@ddt.ddt
@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class LearnerThreadViewAPITest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for LearnerThreadView list"""

    def setUp(self):
        """
        Sets up the test case
        """
        super().setUp()
        self.author = self.user
        self.remove_keys = [
            "abuse_flaggers",
            "body",
            "children",
            "commentable_id",
            "endorsed",
            "last_activity_at",
            "resp_total",
            "thread_type",
            "user_id",
            "username",
            "votes",
        ]
        self.replace_keys = [
            {"from": "unread_comments_count", "to": "unread_comment_count"},
            {"from": "comments_count", "to": "comment_count"},
        ]
        self.add_keys = [
            {"key": "author", "value": self.author.username},
            {"key": "abuse_flagged", "value": False},
            {"key": "author_label", "value": None},
            {"key": "can_delete", "value": True},
            {"key": "close_reason", "value": None},
            {
                "key": "comment_list_url",
                "value": "http://testserver/api/discussion/v1/comments/?thread_id=test_thread",
            },
            {
                "key": "editable_fields",
                "value": [
                    "abuse_flagged",
                    "anonymous",
                    "copy_link",
                    "following",
                    "raw_body",
                    "read",
                    "title",
                    "topic_id",
                    "type",
                ],
            },
            {"key": "endorsed_comment_list_url", "value": None},
            {"key": "following", "value": False},
            {"key": "group_name", "value": None},
            {"key": "has_endorsed", "value": False},
            {"key": "last_edit", "value": None},
            {"key": "non_endorsed_comment_list_url", "value": None},
            {"key": "preview_body", "value": "Test body"},
            {"key": "raw_body", "value": "Test body"},
            {"key": "rendered_body", "value": "<p>Test body</p>"},
            {"key": "response_count", "value": 0},
            {"key": "topic_id", "value": "test_topic"},
            {"key": "type", "value": "discussion"},
            {
                "key": "users",
                "value": {
                    self.user.username: {
                        "profile": {
                            "image": {
                                "has_image": False,
                                "image_url_full": "http://testserver/static/default_500.png",
                                "image_url_large": "http://testserver/static/default_120.png",
                                "image_url_medium": "http://testserver/static/default_50.png",
                                "image_url_small": "http://testserver/static/default_30.png",
                            }
                        }
                    }
                },
            },
            {"key": "vote_count", "value": 4},
            {"key": "voted", "value": False},
        ]
        self.url = reverse(
            "discussion_learner_threads", kwargs={"course_id": str(self.course.id)}
        )
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_get_user_active_threads = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user_active_threads"
        ).start()
        self.mock_get_course_id_by_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread"
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_update_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_thread"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def update_thread(self, thread):
        """
        This function updates the thread by adding and remove some keys.
        Value of these keys has been defined in setUp function
        """
        for element in self.add_keys:
            thread[element["key"]] = element["value"]
        for pair in self.replace_keys:
            thread[pair["to"]] = thread.pop(pair["from"])
        for key in self.remove_keys:
            thread.pop(key)
        thread["comment_count"] += 1
        return thread

    def test_basic(self):
        """
        Tests the data is fetched correctly

        Note: test_basic is required as the name because DiscussionAPIViewTestMixin
              calls this test case automatically
        """
        self.register_get_user_response(self.user)
        expected_cs_comments_response = {
            "collection": [
                make_minimal_cs_thread(
                    {
                        "id": "test_thread",
                        "course_id": str(self.course.id),
                        "commentable_id": "test_topic",
                        "user_id": str(self.user.id),
                        "username": self.user.username,
                        "created_at": "2015-04-28T00:00:00Z",
                        "updated_at": "2015-04-28T11:11:11Z",
                        "title": "Test Title",
                        "body": "Test body",
                        "votes": {"up_count": 4},
                        "comments_count": 5,
                        "unread_comments_count": 3,
                        "closed_by_label": None,
                        "edit_by_label": None,
                    }
                )
            ],
            "page": 1,
            "num_pages": 1,
        }
        self.register_user_active_threads(self.user.id, expected_cs_comments_response)
        self.url += f"?username={self.user.username}"
        response = self.client.get(self.url)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        expected_api_response = expected_cs_comments_response["collection"]

        for thread in expected_api_response:
            self.update_thread(thread)

        assert response_data["results"] == expected_api_response
        assert response_data["pagination"] == {
            "next": None,
            "previous": None,
            "count": 1,
            "num_pages": 1,
        }
        params = {
            "course_id": "course-v1:x+y+z",
            "page": 1,
            "per_page": 10,
            "user_id": "2",
            "group_id": None,
            "count_flagged": False,
            "thread_type": None,
            "sort_key": "activity",
        }
        self.mock_get_user_active_threads.assert_called_once_with(**params)

    def test_not_authenticated(self):
        """
        Override the parent implementation of this, we JWT auth for this API
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def test_no_username_given(self):
        """
        Tests that 404 response is returned when no username is passed
        """
        response = self.client.get(self.url)
        assert response.status_code == 404

    def test_not_authenticated(self):
        """
        This test is called by DiscussionAPIViewTestMixin and is not required in
        our case
        """
        assert True

    @ddt.data("None", "discussion", "question")
    def test_thread_type_by(self, thread_type):
        """
        Tests the thread_type parameter

        Arguments:
            thread_type (str): Value of thread_type can be 'None',
                          'discussion' and 'question'
        """
        threads = [
            make_minimal_cs_thread(
                {
                    "id": "test_thread",
                    "course_id": str(self.course.id),
                    "commentable_id": "test_topic",
                    "user_id": str(self.user.id),
                    "username": self.user.username,
                    "created_at": "2015-04-28T00:00:00Z",
                    "updated_at": "2015-04-28T11:11:11Z",
                    "title": "Test Title",
                    "body": "Test body",
                    "votes": {"up_count": 4},
                    "comments_count": 5,
                    "unread_comments_count": 3,
                }
            )
        ]
        expected_cs_comments_response = {
            "collection": threads,
            "page": 1,
            "num_pages": 1,
        }
        self.register_get_user_response(self.user)
        self.register_user_active_threads(self.user.id, expected_cs_comments_response)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "username": self.user.username,
                "thread_type": thread_type,
            },
        )
        assert response.status_code == 200
        params = {
            "course_id": "course-v1:x+y+z",
            "page": 1,
            "per_page": 10,
            "user_id": "2",
            "group_id": None,
            "count_flagged": False,
            "thread_type": thread_type,
            "sort_key": "activity",
        }
        self.mock_get_user_active_threads.assert_called_once_with(**params)

    @ddt.data(
        ("last_activity_at", "activity"),
        ("comment_count", "comments"),
        ("vote_count", "votes"),
    )
    @ddt.unpack
    def test_order_by(self, http_query, cc_query):
        """
        Tests the order_by parameter for active threads

        Arguments:
            http_query (str): Query string sent in the http request
            cc_query (str): Query string used for the comments client service
        """
        threads = [
            make_minimal_cs_thread(
                {
                    "id": "test_thread",
                    "course_id": str(self.course.id),
                    "commentable_id": "test_topic",
                    "user_id": str(self.user.id),
                    "username": self.user.username,
                    "created_at": "2015-04-28T00:00:00Z",
                    "updated_at": "2015-04-28T11:11:11Z",
                    "title": "Test Title",
                    "body": "Test body",
                    "votes": {"up_count": 4},
                    "comments_count": 5,
                    "unread_comments_count": 3,
                }
            )
        ]
        expected_cs_comments_response = {
            "collection": threads,
            "page": 1,
            "num_pages": 1,
        }
        self.register_get_user_response(self.user)
        self.register_user_active_threads(self.user.id, expected_cs_comments_response)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "username": self.user.username,
                "order_by": http_query,
            },
        )
        assert response.status_code == 200
        params = {
            "course_id": "course-v1:x+y+z",
            "page": 1,
            "per_page": 10,
            "user_id": "2",
            "group_id": None,
            "count_flagged": False,
            "thread_type": None,
            "sort_key": cc_query,
        }
        self.mock_get_user_active_threads.assert_called_once_with(**params)

    @ddt.data("flagged", "unanswered", "unread", "unresponded")
    def test_status_by(self, post_status):
        """
        Tests the post_status parameter

        Arguments:
            post_status (str): Value of post_status can be 'flagged',
                          'unanswered' and 'unread'
        """
        threads = [
            make_minimal_cs_thread(
                {
                    "id": "test_thread",
                    "course_id": str(self.course.id),
                    "commentable_id": "test_topic",
                    "user_id": str(self.user.id),
                    "username": self.user.username,
                    "created_at": "2015-04-28T00:00:00Z",
                    "updated_at": "2015-04-28T11:11:11Z",
                    "title": "Test Title",
                    "body": "Test body",
                    "votes": {"up_count": 4},
                    "comments_count": 5,
                    "unread_comments_count": 3,
                }
            )
        ]
        expected_cs_comments_response = {
            "collection": threads,
            "page": 1,
            "num_pages": 1,
        }
        self.register_get_user_response(self.user)
        self.register_user_active_threads(self.user.id, expected_cs_comments_response)
        response = self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "username": self.user.username,
                "status": post_status,
            },
        )
        if post_status == "flagged":
            assert response.status_code == 403
        else:
            assert response.status_code == 200
            params = {
                "course_id": "course-v1:x+y+z",
                "page": 1,
                "per_page": 10,
                "user_id": "2",
                "group_id": None,
                "count_flagged": False,
                "thread_type": None,
                "sort_key": "activity",
                post_status: True,
            }
            self.mock_get_user_active_threads.assert_called_once_with(**params)


@ddt.ddt
@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetListTest(
    DiscussionAPIViewTestMixin, ModuleStoreTestCase, ProfileImageTestMixin
):
    """Tests for CommentViewSet list"""

    def setUp(self):
        super().setUp()
        self.author = UserFactory.create()
        self.url = reverse("comment-list")
        self.thread_id = "test_thread"
        self.storage = get_profile_image_storage()
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_get_course_id_by_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread",
            return_value=str(self.course.id),
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_delete_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.delete_thread"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def create_source_comment(self, overrides=None):
        """
        Create a sample source cs_comment
        """
        comment = make_minimal_cs_comment(
            {
                "id": "test_comment",
                "thread_id": self.thread_id,
                "user_id": str(self.user.id),
                "username": self.user.username,
                "created_at": "2015-05-11T00:00:00Z",
                "updated_at": "2015-05-11T11:11:11Z",
                "body": "Test body",
                "votes": {"up_count": 4},
            }
        )

        comment.update(overrides or {})
        return comment

    def make_minimal_cs_thread(self, overrides=None):
        """
        Create a thread with the given overrides, plus the course_id if not
        already in overrides.
        """
        overrides = overrides.copy() if overrides else {}
        overrides.setdefault("course_id", str(self.course.id))
        return make_minimal_cs_thread(overrides)

    def expected_response_comment(self, overrides=None):
        """
        create expected response data
        """
        response_data = {
            "id": "test_comment",
            "thread_id": self.thread_id,
            "parent_id": None,
            "author": self.author.username,
            "author_label": None,
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
            "raw_body": "dummy",
            "rendered_body": "<p>dummy</p>",
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "abuse_flagged": False,
            "abuse_flagged_any_user": None,
            "voted": False,
            "vote_count": 0,
            "children": [],
            "editable_fields": ["abuse_flagged", "voted"],
            "child_count": 0,
            "can_delete": True,
            "anonymous": False,
            "anonymous_to_peers": False,
            "last_edit": None,
            "edit_by_label": None,
            "profile_image": {
                "has_image": False,
                "image_url_full": "http://testserver/static/default_500.png",
                "image_url_large": "http://testserver/static/default_120.png",
                "image_url_medium": "http://testserver/static/default_50.png",
                "image_url_small": "http://testserver/static/default_30.png",
            },
        }
        response_data.update(overrides or {})
        return response_data

    def test_thread_id_missing(self):
        response = self.client.get(self.url)
        self.assert_response_correct(
            response,
            400,
            {
                "field_errors": {
                    "thread_id": {"developer_message": "This field is required."}
                }
            },
        )

    # def test_404(self):
    #     self.register_get_thread_error_response(self.thread_id, 404)
    #     response = self.client.get(self.url, {"thread_id": self.thread_id})
    #     self.assert_response_correct(
    #         response, 404, {"developer_message": "Thread not found."}
    #     )

    def test_basic(self):
        self.register_get_user_response(self.user, upvoted_ids=["test_comment"])
        source_comments = [
            self.create_source_comment(
                {"user_id": str(self.author.id), "username": self.author.username}
            )
        ]
        expected_comments = [
            self.expected_response_comment(
                overrides={
                    "voted": True,
                    "vote_count": 4,
                    "raw_body": "Test body",
                    "can_delete": False,
                    "rendered_body": "<p>Test body</p>",
                    "created_at": "2015-05-11T00:00:00Z",
                    "updated_at": "2015-05-11T11:11:11Z",
                }
            )
        ]
        self.register_get_thread_response(
            {
                "id": self.thread_id,
                "course_id": str(self.course.id),
                "thread_type": "discussion",
                "children": source_comments,
                "resp_total": 100,
            }
        )
        response = self.client.get(self.url, {"thread_id": self.thread_id})
        next_link = (
            "http://testserver/api/discussion/v1/comments/?page=2&thread_id={}".format(
                self.thread_id
            )
        )
        self.assert_response_correct(
            response,
            200,
            make_paginated_api_response(
                results=expected_comments,
                count=100,
                num_pages=10,
                next_link=next_link,
                previous_link=None,
            ),
        )
        params = {
            "recursive": False,
            "with_responses": True,
            "user_id": str(self.user.id),
            "mark_as_read": False,
            "resp_skip": 0,
            "resp_limit": 10,
            "reverse_order": False,
            "merge_question_type_responses": False,
        }
        self.mock_get_thread.assert_called_once_with(
            thread_id="test_thread", params=params, course_id=str(self.course.id)
        )

    def test_pagination(self):
        """
        Test that pagination parameters are correctly plumbed through to the
        comments service and that a 404 is correctly returned if a page past the
        end is requested
        """
        self.register_get_user_response(self.user)
        self.register_get_thread_response(
            make_minimal_cs_thread(
                {
                    "id": self.thread_id,
                    "course_id": str(self.course.id),
                    "thread_type": "discussion",
                    "resp_total": 10,
                }
            )
        )
        response = self.client.get(
            self.url, {"thread_id": self.thread_id, "page": "18", "page_size": "4"}
        )
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Page not found (No results on this page)."},
        )
        params = {
            "recursive": False,
            "with_responses": True,
            "user_id": str(self.user.id),
            "mark_as_read": False,
            "resp_skip": 68,
            "resp_limit": 4,
            "reverse_order": False,
            "merge_question_type_responses": False,
        }
        self.mock_get_thread.assert_called_once_with(
            thread_id="test_thread", params=params, course_id=str(self.course.id)
        )

    def test_question_content_with_merge_question_type_responses(self):
        self.register_get_user_response(self.user)
        thread = self.make_minimal_cs_thread(
            {
                "thread_type": "question",
                "children": [
                    make_minimal_cs_comment(
                        {
                            "id": "endorsed_comment",
                            "user_id": self.user.id,
                            "username": self.user.username,
                            "endorsed": True,
                        }
                    ),
                    make_minimal_cs_comment(
                        {
                            "id": "non_endorsed_comment",
                            "user_id": self.user.id,
                            "username": self.user.username,
                            "endorsed": False,
                        }
                    ),
                ],
                "resp_total": 2,
            }
        )
        self.register_get_thread_response(thread)
        response = self.client.get(
            self.url, {"thread_id": thread["id"], "merge_question_type_responses": True}
        )
        parsed_content = json.loads(response.content.decode("utf-8"))
        assert parsed_content["results"][0]["id"] == "endorsed_comment"
        assert parsed_content["results"][1]["id"] == "non_endorsed_comment"
        params = {
            "recursive": False,
            "with_responses": True,
            "user_id": str(self.user.id),
            "mark_as_read": False,
            "resp_skip": 0,
            "resp_limit": 10,
            "reverse_order": False,
            "merge_question_type_responses": True,
        }
        self.mock_get_thread.assert_called_once_with(
            thread_id=thread["id"], params=params, course_id=str(self.course.id)
        )

    @ddt.data(
        (True, "endorsed_comment"),
        ("true", "endorsed_comment"),
        ("1", "endorsed_comment"),
        (False, "non_endorsed_comment"),
        ("false", "non_endorsed_comment"),
        ("0", "non_endorsed_comment"),
    )
    @ddt.unpack
    def test_question_content(self, endorsed, comment_id):
        self.register_get_user_response(self.user)
        thread = self.make_minimal_cs_thread(
            {
                "thread_type": "question",
                "endorsed_responses": [
                    make_minimal_cs_comment(
                        {
                            "id": "endorsed_comment",
                            "user_id": self.user.id,
                            "username": self.user.username,
                        }
                    )
                ],
                "non_endorsed_responses": [
                    make_minimal_cs_comment(
                        {
                            "id": "non_endorsed_comment",
                            "user_id": self.user.id,
                            "username": self.user.username,
                        }
                    )
                ],
                "non_endorsed_resp_total": 1,
            }
        )
        self.register_get_thread_response(thread)
        response = self.client.get(
            self.url,
            {
                "thread_id": thread["id"],
                "endorsed": endorsed,
            },
        )
        parsed_content = json.loads(response.content.decode("utf-8"))
        assert parsed_content["results"][0]["id"] == comment_id

        params = {
            "recursive": False,
            "with_responses": True,
            "user_id": str(self.user.id),
            "mark_as_read": False,
            "resp_skip": 0,
            "resp_limit": 10,
            "reverse_order": False,
            "merge_question_type_responses": False,
        }
        self.mock_get_thread.assert_called_once_with(
            thread_id=thread["id"], params=params, course_id=str(self.course.id)
        )

    def test_question_invalid_endorsed(self):
        response = self.client.get(
            self.url, {"thread_id": self.thread_id, "endorsed": "invalid-boolean"}
        )
        self.assert_response_correct(
            response,
            400,
            {
                "field_errors": {
                    "endorsed": {"developer_message": "Invalid Boolean Value."}
                }
            },
        )

    def test_question_missing_endorsed(self):
        self.register_get_user_response(self.user)
        thread = self.make_minimal_cs_thread(
            {
                "thread_type": "question",
                "endorsed_responses": [
                    make_minimal_cs_comment({"id": "endorsed_comment"})
                ],
                "non_endorsed_responses": [
                    make_minimal_cs_comment({"id": "non_endorsed_comment"})
                ],
                "non_endorsed_resp_total": 1,
            }
        )
        self.register_get_thread_response(thread)
        response = self.client.get(self.url, {"thread_id": thread["id"]})
        self.assert_response_correct(
            response,
            400,
            {
                "field_errors": {
                    "endorsed": {
                        "developer_message": "This field is required for question threads."
                    }
                }
            },
        )

    @ddt.data(("discussion", False), ("question", True))
    @ddt.unpack
    def test_child_comments_count(self, thread_type, merge_question_type_responses):
        self.register_get_user_response(self.user)
        response_1 = make_minimal_cs_comment(
            {
                "id": "test_response_1",
                "thread_id": self.thread_id,
                "user_id": str(self.author.id),
                "username": self.author.username,
                "child_count": 2,
            }
        )
        response_2 = make_minimal_cs_comment(
            {
                "id": "test_response_2",
                "thread_id": self.thread_id,
                "user_id": str(self.author.id),
                "username": self.author.username,
                "child_count": 3,
            }
        )
        thread = self.make_minimal_cs_thread(
            {
                "id": self.thread_id,
                "course_id": str(self.course.id),
                "thread_type": thread_type,
                "children": [response_1, response_2],
                "resp_total": 2,
                "comments_count": 8,
                "unread_comments_count": 0,
            }
        )
        self.register_get_thread_response(thread)
        response = self.client.get(
            self.url,
            {
                "thread_id": self.thread_id,
                "merge_question_type_responses": merge_question_type_responses,
            },
        )
        expected_comments = [
            self.expected_response_comment(
                overrides={
                    "id": "test_response_1",
                    "child_count": 2,
                    "can_delete": False,
                }
            ),
            self.expected_response_comment(
                overrides={
                    "id": "test_response_2",
                    "child_count": 3,
                    "can_delete": False,
                }
            ),
        ]
        self.assert_response_correct(
            response,
            200,
            {
                "results": expected_comments,
                "pagination": {
                    "count": 2,
                    "next": None,
                    "num_pages": 1,
                    "previous": None,
                },
            },
        )
        params = {
            "recursive": False,
            "with_responses": True,
            "user_id": str(self.user.id),
            "mark_as_read": False,
            "resp_skip": 0,
            "resp_limit": 10,
            "reverse_order": False,
            "merge_question_type_responses": merge_question_type_responses,
        }
        self.mock_get_thread.assert_called_once_with(
            thread_id=thread["id"], params=params, course_id=str(self.course.id)
        )

    def test_profile_image_requested_field(self):
        """
        Tests all comments retrieved have user profile image details if called in requested_fields
        """
        source_comments = [self.create_source_comment()]
        self.register_get_thread_response(
            {
                "id": self.thread_id,
                "course_id": str(self.course.id),
                "thread_type": "discussion",
                "children": source_comments,
                "resp_total": 100,
            }
        )
        self.register_get_user_response(self.user, upvoted_ids=["test_comment"])
        self.create_profile_image(self.user, get_profile_image_storage())

        response = self.client.get(
            self.url, {"thread_id": self.thread_id, "requested_fields": "profile_image"}
        )
        assert response.status_code == 200
        response_comments = json.loads(response.content.decode("utf-8"))["results"]
        for response_comment in response_comments:
            expected_profile_data = self.get_expected_user_profile(
                response_comment["author"]
            )
            response_users = response_comment["users"]
            assert expected_profile_data == response_users[response_comment["author"]]

    def test_profile_image_requested_field_endorsed_comments(self):
        """
        Tests all comments have user profile image details for both author and endorser
        if called in requested_fields for endorsed threads
        """
        endorser_user = UserFactory.create(password=self.password)
        # Ensure that parental controls don't apply to this user
        endorser_user.profile.year_of_birth = 1970
        endorser_user.profile.save()

        self.register_get_user_response(self.user)
        thread = self.make_minimal_cs_thread(
            {
                "thread_type": "question",
                "endorsed_responses": [
                    make_minimal_cs_comment(
                        {
                            "id": "endorsed_comment",
                            "user_id": self.user.id,
                            "username": self.user.username,
                            "endorsed": True,
                            "endorsement": {
                                "user_id": endorser_user.id,
                                "time": "2016-05-10T08:51:28Z",
                            },
                        }
                    )
                ],
                "non_endorsed_responses": [
                    make_minimal_cs_comment(
                        {
                            "id": "non_endorsed_comment",
                            "user_id": self.user.id,
                            "username": self.user.username,
                        }
                    )
                ],
                "non_endorsed_resp_total": 1,
            }
        )
        self.register_get_thread_response(thread)
        self.create_profile_image(self.user, get_profile_image_storage())
        self.create_profile_image(endorser_user, get_profile_image_storage())

        response = self.client.get(
            self.url,
            {
                "thread_id": thread["id"],
                "endorsed": True,
                "requested_fields": "profile_image",
            },
        )
        assert response.status_code == 200
        response_comments = json.loads(response.content.decode("utf-8"))["results"]
        for response_comment in response_comments:
            expected_author_profile_data = self.get_expected_user_profile(
                response_comment["author"]
            )
            expected_endorser_profile_data = self.get_expected_user_profile(
                response_comment["endorsed_by"]
            )
            response_users = response_comment["users"]
            assert (
                expected_author_profile_data
                == response_users[response_comment["author"]]
            )
            assert (
                expected_endorser_profile_data
                == response_users[response_comment["endorsed_by"]]
            )

    def test_profile_image_request_for_null_endorsed_by(self):
        """
        Tests if 'endorsed' is True but 'endorsed_by' is null, the api does not crash.
        This is the case for some old/stale data in prod/stage environments.
        """
        self.register_get_user_response(self.user)
        thread = self.make_minimal_cs_thread(
            {
                "thread_type": "question",
                "endorsed_responses": [
                    make_minimal_cs_comment(
                        {
                            "id": "endorsed_comment",
                            "user_id": self.user.id,
                            "username": self.user.username,
                            "endorsed": True,
                        }
                    )
                ],
                "non_endorsed_resp_total": 0,
            }
        )
        self.register_get_thread_response(thread)
        self.create_profile_image(self.user, get_profile_image_storage())

        response = self.client.get(
            self.url,
            {
                "thread_id": thread["id"],
                "endorsed": True,
                "requested_fields": "profile_image",
            },
        )
        assert response.status_code == 200
        response_comments = json.loads(response.content.decode("utf-8"))["results"]
        for response_comment in response_comments:
            expected_author_profile_data = self.get_expected_user_profile(
                response_comment["author"]
            )
            response_users = response_comment["users"]
            assert (
                expected_author_profile_data
                == response_users[response_comment["author"]]
            )
            assert response_comment["endorsed_by"] not in response_users

    def test_reverse_order_sort(self):
        """
        Tests if reverse_order param is passed to cs comments service
        """
        self.register_get_user_response(self.user, upvoted_ids=["test_comment"])
        source_comments = [
            self.create_source_comment(
                {"user_id": str(self.author.id), "username": self.author.username}
            )
        ]
        self.register_get_thread_response(
            {
                "id": self.thread_id,
                "course_id": str(self.course.id),
                "thread_type": "discussion",
                "children": source_comments,
                "resp_total": 100,
            }
        )
        self.client.get(self.url, {"thread_id": self.thread_id, "reverse_order": True})
        params = {
            "recursive": False,
            "with_responses": True,
            "user_id": str(self.user.id),
            "mark_as_read": False,
            "resp_skip": 0,
            "resp_limit": 10,
            "reverse_order": "True",
            "merge_question_type_responses": False,
        }
        self.mock_get_thread.assert_called_once_with(
            thread_id=self.thread_id, params=params, course_id=str(self.course.id)
        )


@httpretty.activate
@disable_signal(api, "comment_deleted")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetDeleteTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for ThreadViewSet delete"""

    def setUp(self):
        super().setUp()
        self.url = reverse("comment-detail", kwargs={"comment_id": "test_comment"})
        self.comment_id = "test_comment"
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_get_course_id_by_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread",
            return_value=str(self.course.id),
        ).start()
        self.mock_get_course_id_by_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_course_id_by_comment",
            return_value=str(self.course.id),
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_delete_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.delete_thread"
        ).start()
        self.mock_delete_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.delete_comment"
        ).start()
        self.mock_get_parent_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_parent_comment"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_basic(self):
        self.register_get_user_response(self.user)
        cs_thread = make_minimal_cs_thread(
            {
                "id": "test_thread",
                "course_id": str(self.course.id),
            }
        )
        self.register_get_thread_response(cs_thread)
        cs_comment = make_minimal_cs_comment(
            {
                "id": self.comment_id,
                "course_id": cs_thread["course_id"],
                "thread_id": cs_thread["id"],
                "username": self.user.username,
                "user_id": str(self.user.id),
            }
        )
        self.register_get_comment_response(cs_comment)
        self.register_delete_comment_response(self.comment_id)
        response = self.client.delete(self.url)
        assert response.status_code == 204
        assert response.content == b""
        self.mock_delete_comment.assert_called_once_with(
            comment_id=self.comment_id, course_id=cs_thread["course_id"]
        )

    def test_delete_nonexistent_comment(self):
        try:
            self.register_get_comment_error_response(self.comment_id, 404)
        except Exception as e:
            assert e == "404 Not Found"


@httpretty.activate
@disable_signal(api, "comment_created")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@mock.patch(
    "lms.djangoapps.discussion.signals.handlers.send_response_notifications",
    new=mock.Mock(),
)
class CommentViewSetCreateTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for CommentViewSet create"""

    def setUp(self):
        super().setUp()
        self.url = reverse("comment-list")
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_get_course_id_by_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread",
            return_value=str(self.course.id),
        ).start()
        self.mock_get_course_id_by_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_course_id_by_comment",
            return_value=str(self.course.id),
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_update_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_thread"
        ).start()
        self.mock_get_parent_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_parent_comment"
        ).start()
        self.mock_update_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_comment"
        ).start()
        self.mock_create_parent_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_parent_comment"
        ).start()
        self.mock_create_child_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_child_comment"
        ).start()
        self.addCleanup(mock.patch.stopall)

    def test_basic(self):
        self.register_get_user_response(self.user)
        self.register_thread()
        self.register_comment()
        request_data = {
            "thread_id": "test_thread",
            "raw_body": "Test body",
        }
        expected_response_data = {
            "id": "test_comment",
            "author": self.user.username,
            "author_label": None,
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
            "raw_body": "Original body",
            "rendered_body": "<p>Original body</p>",
            "abuse_flagged": False,
            "voted": False,
            "vote_count": 0,
            "editable_fields": ["abuse_flagged", "anonymous", "raw_body"],
            "can_delete": True,
            "anonymous": False,
            "anonymous_to_peers": False,
            "last_edit": None,
            "edit_by_label": None,
            "thread_id": "test_thread",
            "parent_id": None,
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "child_count": 0,
            "children": [],
            "abuse_flagged_any_user": None,
            "profile_image": {
                "has_image": False,
                "image_url_full": "http://testserver/static/default_500.png",
                "image_url_large": "http://testserver/static/default_120.png",
                "image_url_medium": "http://testserver/static/default_50.png",
                "image_url_small": "http://testserver/static/default_30.png",
            },
        }
        response = self.client.post(
            self.url, json.dumps(request_data), content_type="application/json"
        )
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == expected_response_data
        self.mock_create_parent_comment.assert_called_once_with(
            "test_thread", "Test body", "2", "course-v1:x+y+z", False, False
        )

    def test_error(self):
        response = self.client.post(
            self.url, json.dumps({}), content_type="application/json"
        )
        expected_response_data = {
            "field_errors": {
                "thread_id": {"developer_message": "This field is required."}
            }
        }
        assert response.status_code == 400
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == expected_response_data

    def test_closed_thread(self):
        self.register_get_user_response(self.user)
        self.register_thread({"closed": True})
        self.register_comment()
        request_data = {"thread_id": "test_thread", "raw_body": "Test body"}
        response = self.client.post(
            self.url, json.dumps(request_data), content_type="application/json"
        )
        assert response.status_code == 403


@ddt.ddt
@disable_signal(api, "comment_edited")
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetPartialUpdateTest(
    DiscussionAPIViewTestMixin, ModuleStoreTestCase, PatchMediaTypeMixin
):
    """Tests for CommentViewSet partial_update"""

    def setUp(self):
        self.unsupported_media_type = JSONParser.media_type
        super().setUp()
        httpretty.reset()
        httpretty.enable()
        self.addCleanup(httpretty.reset)
        self.addCleanup(httpretty.disable)
        mock.patch(
            "lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled",
            return_value=True,
        ).start()
        self.mock_get_user = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.get_user"
        ).start()
        self.mock_get_course_id_by_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread",
            return_value=str(self.course.id),
        ).start()
        self.mock_get_course_id_by_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_course_id_by_comment",
            return_value=str(self.course.id),
        ).start()
        self.mock_update_comment_flag = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.comment.forum_api.update_comment_flag",
            return_value=str(self.course.id),
        ).start()
        self.mock_get_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread"
        ).start()
        self.mock_update_thread = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_thread"
        ).start()
        self.mock_get_parent_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_parent_comment"
        ).start()
        self.mock_update_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_comment"
        ).start()
        self.mock_create_parent_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_parent_comment"
        ).start()
        self.mock_create_child_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_child_comment"
        ).start()
        self.mock_update_thread_flag = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.update_thread_flag"
        ).start()
        self.mock_update_thread_flag_in_comment = mock.patch(
            "openedx.core.djangoapps.django_comment_common.comment_client.comment.forum_api.update_thread_flag"
        ).start()
        self.addCleanup(mock.patch.stopall)
        self.register_get_user_response(self.user)
        self.url = reverse("comment-detail", kwargs={"comment_id": "test_comment"})

    def expected_response_data(self, overrides=None):
        """
        create expected response data from comment update endpoint
        """
        response_data = {
            "id": "test_comment",
            "thread_id": "test_thread",
            "parent_id": None,
            "author": self.user.username,
            "author_label": None,
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
            "raw_body": "Original body",
            "rendered_body": "<p>Original body</p>",
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "abuse_flagged": False,
            "abuse_flagged_any_user": None,
            "voted": False,
            "vote_count": 0,
            "children": [],
            "editable_fields": [],
            "child_count": 0,
            "can_delete": True,
            "anonymous": False,
            "anonymous_to_peers": False,
            "last_edit": None,
            "edit_by_label": None,
            "profile_image": {
                "has_image": False,
                "image_url_full": "http://testserver/static/default_500.png",
                "image_url_large": "http://testserver/static/default_120.png",
                "image_url_medium": "http://testserver/static/default_50.png",
                "image_url_small": "http://testserver/static/default_30.png",
            },
        }
        response_data.update(overrides or {})
        return response_data

    def test_basic(self):
        self.register_thread()
        self.register_comment(
            {"created_at": "Test Created Date", "updated_at": "Test Updated Date"}
        )
        request_data = {"raw_body": "Edited body"}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_response_data(
            {
                "raw_body": "Original body",
                "rendered_body": "<p>Original body</p>",
                "editable_fields": ["abuse_flagged", "anonymous", "raw_body"],
                "created_at": "Test Created Date",
                "updated_at": "Test Updated Date",
            }
        )
        self.mock_update_comment.assert_called_once_with(
            comment_id="test_comment",
            body="Edited body",
            course_id=str(self.course.id),
            user_id=str(self.user.id),
            anonymous=False,
            anonymous_to_peers=False,
            endorsed=False,
            editing_user_id=str(self.user.id),
            course_key=str(self.course.id),
        )

    def test_error(self):
        self.register_thread()
        self.register_comment()
        request_data = {"raw_body": ""}
        response = self.request_patch(request_data)
        expected_response_data = {
            "field_errors": {
                "raw_body": {"developer_message": "This field may not be blank."}
            }
        }
        assert response.status_code == 400
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == expected_response_data

    @ddt.data(
        ("abuse_flagged", True),
        ("abuse_flagged", False),
    )
    @ddt.unpack
    def test_closed_thread(self, field, value):
        self.register_thread({"closed": True})
        self.register_comment()
        self.register_flag_response("comment", "test_comment")
        request_data = {field: value}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_response_data(
            {
                "abuse_flagged": value,
                "abuse_flagged_any_user": None,
                "editable_fields": ["abuse_flagged"],
            }
        )
        if value:
            self.mock_update_comment_flag.assert_called_once_with(
                "test_comment",
                "flag",
                str(self.user.id),
                str(self.course.id),
            )

    @ddt.data(
        ("raw_body", "Edited body"),
        ("voted", True),
        ("following", True),
    )
    @ddt.unpack
    def test_closed_thread_error(self, field, value):
        self.register_thread({"closed": True})
        self.register_comment()
        request_data = {field: value}
        response = self.request_patch(request_data)
        assert response.status_code == 400
