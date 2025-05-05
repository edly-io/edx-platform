# pylint: skip-file
"""
Tests for Discussion API views
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
from lms.djangoapps.discussion.django_comment_client.tests.mixins import (
    MockForumApiMixin,
)
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
from lms.djangoapps.discussion.tests.utils import (
    make_minimal_cs_comment,
    make_minimal_cs_thread,
)
from lms.djangoapps.discussion.django_comment_client.tests.utils import (
    ForumsEnableMixin,
    config_course_discussions,
    topic_name_to_id,
)
from lms.djangoapps.discussion.rest_api import api
from lms.djangoapps.discussion.rest_api.tests.utils import (
    CommentsServiceMockMixin,
    ForumMockUtilsMixin,
    ProfileImageTestMixin,
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


class DiscussionAPIViewTestMixin(ForumsEnableMixin, ForumMockUtilsMixin, UrlResetMixin):
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

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        super().disposeForumMocks()

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

    def test_basic(self):
        self.register_get_user_response(self.user)
        self.register_thread(
            {
                "created_at": "Test Created Date",
                "updated_at": "Test Updated Date",
                "read": True,
                "resp_total": 2,
            }
        )
        request_data = {"raw_body": "Edited body"}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        assert response_data == self.expected_thread_data(
            {
                "raw_body": "Edited body",
                "rendered_body": "<p>Edited body</p>",
                "preview_body": "Edited body",
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
                "created_at": "Test Created Date",
                "updated_at": "Test Updated Date",
                "comment_count": 1,
                "read": True,
                "response_count": 2,
            }
        )

        params = {
            "thread_id": "test_thread",
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "thread_type": "discussion",
            "title": "Test Title",
            "body": "Edited body",
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
            "closed": False,
            "pinned": False,
            "read": True,
            "editing_user_id": str(self.user.id),
        }
        self.check_mock_called_with("update_thread", -1, **params)

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

    def test_patch_read_non_owner_user(self):
        self.register_get_user_response(self.user)
        thread_owner_user = UserFactory.create(password=self.password)
        CourseEnrollmentFactory.create(user=thread_owner_user, course_id=self.course.id)
        self.register_thread(
            {
                "username": thread_owner_user.username,
                "user_id": str(thread_owner_user.id),
                "resp_total": 2,
            }
        )
        self.register_read_response(self.user, "thread", "test_thread")

        request_data = {"read": True}
        response = self.request_patch(request_data)
        assert response.status_code == 200
        response_data = json.loads(response.content.decode("utf-8"))
        expected_data = self.expected_thread_data(
            {
                "author": str(thread_owner_user.username),
                "comment_count": 1,
                "can_delete": False,
                "read": True,
                "editable_fields": [
                    "abuse_flagged",
                    "copy_link",
                    "following",
                    "read",
                    "voted",
                ],
                "response_count": 2,
            }
        )
        assert response_data == expected_data


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
                "raw_body": "Edited body",
                "rendered_body": "<p>Edited body</p>",
                "editable_fields": ["abuse_flagged", "anonymous", "raw_body"],
                "created_at": "Test Created Date",
                "updated_at": "Test Updated Date",
            }
        )
        params = {
            "comment_id": "test_comment",
            "body": "Edited body",
            "course_id": str(self.course.id),
            "user_id": str(self.user.id),
            "anonymous": False,
            "anonymous_to_peers": False,
            "endorsed": False,
            "editing_user_id": str(self.user.id),
        }
        self.check_mock_called_with("update_comment", -1, **params)

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
            {"field_errors": {"course_id": {"developer_message": "This field is required."}}}
        )

    def test_404(self):
        response = self.client.get(self.url, {"course_id": "non/existent/course"})
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Course not found."}
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
        self.register_get_threads_response(source_threads, page=1, num_pages=2)
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
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    @ddt.data("unread", "unanswered", "unresponded")
    def test_view_query(self, query):
        threads = [make_minimal_cs_thread()]
        self.register_get_user_response(self.user)
        self.register_get_threads_response(threads, page=1, num_pages=1)
        self.client.get(
            self.url,
            {
                "course_id": str(self.course.id),
                "view": query,
            },
        )
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
            query: True,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_pagination(self):
        self.register_get_user_response(self.user)
        self.register_get_threads_response([], page=1, num_pages=1)
        response = self.client.get(
            self.url, {"course_id": str(self.course.id), "page": "18", "page_size": "4"}
        )
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Page not found (No results on this page)."},
        )
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 18,
            "per_page": 4,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
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
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
            "text": "test search string",
        }
        self.check_mock_called_with(
            "search_threads",
            -1,
            **params,
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
        self.check_mock_called("get_user_threads")

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
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "page": 1,
            "per_page": 10,
            "sort_key": cc_query,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
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
        params = {
            "user_id": str(self.user.id),
            "course_id": str(self.course.id),
            "sort_key": "activity",
            "page": 1,
            "per_page": 10,
        }
        self.check_mock_called_with(
            "get_user_threads",
            -1,
            **params,
        )

    def test_mutually_exclusive(self):
        """
        Tests GET thread_list api does not allow filtering on mutually exclusive parameters
        """
        self.register_get_user_response(self.user)
        self.register_get_threads_search_response([], None, num_pages=0)
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
@disable_signal(api, 'thread_deleted')
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetDeleteTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for ThreadViewSet delete"""

    def setUp(self):
        super().setUp()
        self.url = reverse("thread-detail", kwargs={"thread_id": "test_thread"})
        self.thread_id = "test_thread"

    def test_basic(self):
        self.register_get_user_response(self.user)
        cs_thread = make_minimal_cs_thread({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "username": self.user.username,
            "user_id": str(self.user.id),
        })
        self.register_get_thread_response(cs_thread)
        self.register_delete_thread_response(self.thread_id)
        response = self.client.delete(self.url)
        assert response.status_code == 204
        assert response.content == b''
        params = {
            "thread_id": self.thread_id,
            "course_id": str(self.course.id),
        }
        self.check_mock_called_with('delete_thread', -1, **params)

    def test_delete_nonexistent_thread(self):
        self.register_get_thread_error_response(self.thread_id, 404)
        response = self.client.delete(self.url)
        assert response.status_code == 404



@ddt.ddt
@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetListTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase, ProfileImageTestMixin):
    """Tests for CommentViewSet list"""

    def setUp(self):
        super().setUp()
        self.author = UserFactory.create()
        self.url = reverse("comment-list")
        self.thread_id = "test_thread"
        self.storage = get_profile_image_storage()
        self.set_mock_return_value('get_course_id_by_thread', str(self.course.id))

    def create_source_comment(self, overrides=None):
        """
        Create a sample source cs_comment
        """
        comment = make_minimal_cs_comment({
            "id": "test_comment",
            "thread_id": self.thread_id,
            "user_id": str(self.user.id),
            "username": self.user.username,
            "created_at": "2015-05-11T00:00:00Z",
            "updated_at": "2015-05-11T11:11:11Z",
            "body": "Test body",
            "votes": {"up_count": 4},
        })

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
            {"field_errors": {"thread_id": {"developer_message": "This field is required."}}}
        )

    def test_404(self):
        self.register_get_thread_error_response(self.thread_id, 404)
        response = self.client.get(self.url, {"thread_id": self.thread_id})
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Thread not found."}
        )

    def test_basic(self):
        self.register_get_user_response(self.user, upvoted_ids=["test_comment"])
        source_comments = [
            self.create_source_comment({"user_id": str(self.author.id), "username": self.author.username})
        ]
        expected_comments = [self.expected_response_comment(overrides={
            "voted": True,
            "vote_count": 4,
            "raw_body": "Test body",
            "can_delete": False,
            "rendered_body": "<p>Test body</p>",
            "created_at": "2015-05-11T00:00:00Z",
            "updated_at": "2015-05-11T11:11:11Z",
        })]
        self.register_get_thread_response({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "thread_type": "discussion",
            "children": source_comments,
            "resp_total": 100,
        })
        response = self.client.get(self.url, {"thread_id": self.thread_id})
        next_link = "http://testserver/api/discussion/v1/comments/?page=2&thread_id={}".format(
            self.thread_id
        )
        self.assert_response_correct(
            response,
            200,
            make_paginated_api_response(
                results=expected_comments, count=100, num_pages=10, next_link=next_link, previous_link=None
            )
        )
        params = {
            "thread_id": self.thread_id,
            "params": {
                "resp_skip": 0,
                "resp_limit": 10,
                "user_id": str(self.user.id),
                "mark_as_read": False,
                "recursive": False,
                "with_responses": True,
                "reverse_order": False,
                "merge_question_type_responses": False,
            },
            "course_id": str(self.course.id)
        }
        self.check_mock_called_with('get_thread', -1, **params)

    def test_pagination(self):
        """
        Test that pagination parameters are correctly plumbed through to the
        comments service and that a 404 is correctly returned if a page past the
        end is requested
        """
        self.register_get_user_response(self.user)
        self.register_get_thread_response(make_minimal_cs_thread({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "thread_type": "discussion",
            "resp_total": 10,
        }))
        response = self.client.get(
            self.url,
            {"thread_id": self.thread_id, "page": "18", "page_size": "4"}
        )
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Page not found (No results on this page)."}
        )

        params = {
            "thread_id": self.thread_id,
            "params": {
                "resp_skip": 68,
                "resp_limit": 4,
                "user_id": str(self.user.id),
                "mark_as_read": False,
                "recursive": False,
                "with_responses": True,
                "reverse_order": False,
                "merge_question_type_responses": False,
            },
            "course_id": str(self.course.id)
        }
        self.check_mock_called_with('get_thread', -1, **params)

    def test_question_content_with_merge_question_type_responses(self):
        self.register_get_user_response(self.user)
        thread = self.make_minimal_cs_thread({
            "thread_type": "question",
            "children": [make_minimal_cs_comment({
                "id": "endorsed_comment",
                "user_id": self.user.id,
                "username": self.user.username,
                "endorsed": True,
            }),
                make_minimal_cs_comment({
                    "id": "non_endorsed_comment",
                    "user_id": self.user.id,
                    "username": self.user.username,
                    "endorsed": False,
                })],
            "resp_total": 2,
        })
        self.register_get_thread_response(thread)
        response = self.client.get(self.url, {
            "thread_id": thread["id"],
            "merge_question_type_responses": True
        })
        parsed_content = json.loads(response.content.decode('utf-8'))
        assert parsed_content['results'][0]['id'] == "endorsed_comment"
        assert parsed_content['results'][1]['id'] == "non_endorsed_comment"

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
        thread = self.make_minimal_cs_thread({
            "thread_type": "question",
            "endorsed_responses": [make_minimal_cs_comment({
                "id": "endorsed_comment",
                "user_id": self.user.id,
                "username": self.user.username,
            })],
            "non_endorsed_responses": [make_minimal_cs_comment({
                "id": "non_endorsed_comment",
                "user_id": self.user.id,
                "username": self.user.username,
            })],
            "non_endorsed_resp_total": 1,
        })
        self.register_get_thread_response(thread)
        response = self.client.get(self.url, {
            "thread_id": thread["id"],
            "endorsed": endorsed,
        })
        parsed_content = json.loads(response.content.decode('utf-8'))
        assert parsed_content['results'][0]['id'] == comment_id

    def test_question_invalid_endorsed(self):
        response = self.client.get(self.url, {
            "thread_id": self.thread_id,
            "endorsed": "invalid-boolean"
        })
        self.assert_response_correct(
            response,
            400,
            {"field_errors": {
                "endorsed": {"developer_message": "Invalid Boolean Value."}
            }}
        )

    def test_question_missing_endorsed(self):
        self.register_get_user_response(self.user)
        thread = self.make_minimal_cs_thread({
            "thread_type": "question",
            "endorsed_responses": [make_minimal_cs_comment({"id": "endorsed_comment"})],
            "non_endorsed_responses": [make_minimal_cs_comment({"id": "non_endorsed_comment"})],
            "non_endorsed_resp_total": 1,
        })
        self.register_get_thread_response(thread)
        response = self.client.get(self.url, {
            "thread_id": thread["id"]
        })
        self.assert_response_correct(
            response,
            400,
            {"field_errors": {
                "endorsed": {"developer_message": "This field is required for question threads."}
            }}
        )

    @ddt.data(
        ("discussion", False),
        ("question", True)
    )
    @ddt.unpack
    def test_child_comments_count(self, thread_type, merge_question_type_responses):
        self.register_get_user_response(self.user)
        response_1 = make_minimal_cs_comment({
            "id": "test_response_1",
            "thread_id": self.thread_id,
            "user_id": str(self.author.id),
            "username": self.author.username,
            "child_count": 2,
        })
        response_2 = make_minimal_cs_comment({
            "id": "test_response_2",
            "thread_id": self.thread_id,
            "user_id": str(self.author.id),
            "username": self.author.username,
            "child_count": 3,
        })
        thread = self.make_minimal_cs_thread({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "thread_type": thread_type,
            "children": [response_1, response_2],
            "resp_total": 2,
            "comments_count": 8,
            "unread_comments_count": 0,
        })
        self.register_get_thread_response(thread)
        response = self.client.get(self.url, {
            "thread_id": self.thread_id,
            "merge_question_type_responses": merge_question_type_responses})
        expected_comments = [
            self.expected_response_comment(overrides={"id": "test_response_1", "child_count": 2, "can_delete": False}),
            self.expected_response_comment(overrides={"id": "test_response_2", "child_count": 3, "can_delete": False}),
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
                }
            }
        )

    def test_profile_image_requested_field(self):
        """
        Tests all comments retrieved have user profile image details if called in requested_fields
        """
        source_comments = [self.create_source_comment()]
        self.register_get_thread_response({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "thread_type": "discussion",
            "children": source_comments,
            "resp_total": 100,
        })
        self.register_get_user_response(self.user, upvoted_ids=["test_comment"])
        self.create_profile_image(self.user, get_profile_image_storage())

        response = self.client.get(self.url, {"thread_id": self.thread_id, "requested_fields": "profile_image"})
        assert response.status_code == 200
        response_comments = json.loads(response.content.decode('utf-8'))['results']
        for response_comment in response_comments:
            expected_profile_data = self.get_expected_user_profile(response_comment['author'])
            response_users = response_comment['users']
            assert expected_profile_data == response_users[response_comment['author']]

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
        thread = self.make_minimal_cs_thread({
            "thread_type": "question",
            "endorsed_responses": [make_minimal_cs_comment({
                "id": "endorsed_comment",
                "user_id": self.user.id,
                "username": self.user.username,
                "endorsed": True,
                "endorsement": {"user_id": endorser_user.id, "time": "2016-05-10T08:51:28Z"},
            })],
            "non_endorsed_responses": [make_minimal_cs_comment({
                "id": "non_endorsed_comment",
                "user_id": self.user.id,
                "username": self.user.username,
            })],
            "non_endorsed_resp_total": 1,
        })
        self.register_get_thread_response(thread)
        self.create_profile_image(self.user, get_profile_image_storage())
        self.create_profile_image(endorser_user, get_profile_image_storage())

        response = self.client.get(self.url, {
            "thread_id": thread["id"],
            "endorsed": True,
            "requested_fields": "profile_image",
        })
        assert response.status_code == 200
        response_comments = json.loads(response.content.decode('utf-8'))['results']
        for response_comment in response_comments:
            expected_author_profile_data = self.get_expected_user_profile(response_comment['author'])
            expected_endorser_profile_data = self.get_expected_user_profile(response_comment['endorsed_by'])
            response_users = response_comment['users']
            assert expected_author_profile_data == response_users[response_comment['author']]
            assert expected_endorser_profile_data == response_users[response_comment['endorsed_by']]

    def test_profile_image_request_for_null_endorsed_by(self):
        """
        Tests if 'endorsed' is True but 'endorsed_by' is null, the api does not crash.
        This is the case for some old/stale data in prod/stage environments.
        """
        self.register_get_user_response(self.user)
        thread = self.make_minimal_cs_thread({
            "thread_type": "question",
            "endorsed_responses": [make_minimal_cs_comment({
                "id": "endorsed_comment",
                "user_id": self.user.id,
                "username": self.user.username,
                "endorsed": True,
            })],
            "non_endorsed_resp_total": 0,
        })
        self.register_get_thread_response(thread)
        self.create_profile_image(self.user, get_profile_image_storage())

        response = self.client.get(self.url, {
            "thread_id": thread["id"],
            "endorsed": True,
            "requested_fields": "profile_image",
        })
        assert response.status_code == 200
        response_comments = json.loads(response.content.decode('utf-8'))['results']
        for response_comment in response_comments:
            expected_author_profile_data = self.get_expected_user_profile(response_comment['author'])
            response_users = response_comment['users']
            assert expected_author_profile_data == response_users[response_comment['author']]
            assert response_comment['endorsed_by'] not in response_users

    def test_reverse_order_sort(self):
        """
        Tests if reverse_order param is passed to cs comments service
        """
        self.register_get_user_response(self.user, upvoted_ids=["test_comment"])
        source_comments = [
            self.create_source_comment({"user_id": str(self.author.id), "username": self.author.username})
        ]
        self.register_get_thread_response({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "thread_type": "discussion",
            "children": source_comments,
            "resp_total": 100,
        })
        self.client.get(self.url, {"thread_id": self.thread_id, "reverse_order": True})

        params = {
            "thread_id": self.thread_id,
            "params": {
                "resp_skip": 0,
                "resp_limit": 10,
                "user_id": str(self.user.id),
                "mark_as_read": False,
                "recursive": False,
                "with_responses": True,
                "reverse_order": True,
                "merge_question_type_responses": False,
            },
            "course_id": str(self.course.id)
        }
        self.check_mock_called_with('get_thread', -1, **params)



@httpretty.activate
@disable_signal(api, 'comment_deleted')
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetDeleteTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for ThreadViewSet delete"""

    def setUp(self):
        super().setUp()
        self.url = reverse("comment-detail", kwargs={"comment_id": "test_comment"})
        self.comment_id = "test_comment"
        self.set_mock_return_value('get_course_id_by_thread', str(self.course.id))

    def test_basic(self):
        self.register_get_user_response(self.user)
        cs_thread = make_minimal_cs_thread({
            "id": "test_thread",
            "course_id": str(self.course.id),
        })
        self.register_get_thread_response(cs_thread)
        cs_comment = make_minimal_cs_comment({
            "id": self.comment_id,
            "course_id": cs_thread["course_id"],
            "thread_id": cs_thread["id"],
            "username": self.user.username,
            "user_id": str(self.user.id),
        })
        self.register_get_comment_response(cs_comment)
        self.register_delete_comment_response(self.comment_id)
        response = self.client.delete(self.url)
        assert response.status_code == 204
        assert response.content == b''
        params = {
            "comment_id": self.comment_id,
            "course_id": str(self.course.id),
        }
        self.check_mock_called_with('delete_comment', -1, **params)

    def test_delete_nonexistent_comment(self):
        self.register_get_comment_error_response(self.comment_id, 404)
        response = self.client.delete(self.url)
        assert response.status_code == 404


@httpretty.activate
@disable_signal(api, 'comment_created')
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
@mock.patch("lms.djangoapps.discussion.signals.handlers.send_response_notifications", new=mock.Mock())
class CommentViewSetCreateTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for CommentViewSet create"""

    def setUp(self):
        super().setUp()
        self.url = reverse("comment-list")

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
            "thread_id": "test_thread",
            "parent_id": None,
            "author": self.user.username,
            "author_label": None,
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
            "raw_body": "Test body",
            "rendered_body": "<p>Test body</p>",
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "endorsed_at": None,
            "abuse_flagged": False,
            "abuse_flagged_any_user": None,
            "voted": False,
            "vote_count": 0,
            "children": [],
            "editable_fields": ["abuse_flagged", "anonymous", "raw_body"],
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
        response = self.client.post(
            self.url,
            json.dumps(request_data),
            content_type="application/json"
        )
        assert response.status_code == 200
        response_data = json.loads(response.content.decode('utf-8'))
        assert response_data == expected_response_data
        params = {
            'thread_id': 'test_thread',
            'course_id': str(self.course.id),
            'body': 'Test body',
            'user_id': str(self.user.id),
            'anonymous': False,
            'anonymous_to_peers': False,
        }
        self.check_mock_called_with(
            "create_parent_comment",
            -1,
            **params
        )

    def test_error(self):
        response = self.client.post(
            self.url,
            json.dumps({}),
            content_type="application/json"
        )
        expected_response_data = {
            "field_errors": {"thread_id": {"developer_message": "This field is required."}}
        }
        assert response.status_code == 400
        response_data = json.loads(response.content.decode('utf-8'))
        assert response_data == expected_response_data

    def test_closed_thread(self):
        self.register_get_user_response(self.user)
        self.register_thread({"closed": True})
        self.register_comment()
        request_data = {
            "thread_id": "test_thread",
            "raw_body": "Test body"
        }
        response = self.client.post(
            self.url,
            json.dumps(request_data),
            content_type="application/json"
        )
        assert response.status_code == 403



@httpretty.activate
@disable_signal(api, 'thread_created')
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetCreateTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase):
    """Tests for ThreadViewSet create"""

    def setUp(self):
        super().setUp()
        self.url = reverse("thread-list")

    def test_basic(self):
        self.register_get_user_response(self.user)
        cs_thread = make_minimal_cs_thread({
            "id": "test_thread",
            "username": self.user.username,
            "read": True,
        })
        self.register_post_thread_response(cs_thread)
        request_data = {
            "course_id": str(self.course.id),
            "topic_id": "test_topic",
            "type": "discussion",
            "title": "Test Title",
            "raw_body": "# Test \n This is a very long body but will not be truncated for the preview.",
        }
        response = self.client.post(
            self.url,
            json.dumps(request_data),
            content_type="application/json"
        )
        assert response.status_code == 200
        response_data = json.loads(response.content.decode('utf-8'))
        assert response_data == self.expected_thread_data({
            "read": True,
            "raw_body": "# Test \n This is a very long body but will not be truncated for the preview.",
            "preview_body": "Test This is a very long body but will not be truncated for the preview.",
            "rendered_body": "<h1>Test</h1>\n<p>This is a very long body but will not be truncated for"
                             " the preview.</p>",
        })

        params = {
            'course_id': str(self.course.id),
            'commentable_id': 'test_topic',
            'thread_type': 'discussion',
            'title': 'Test Title',
            'body': '# Test \n This is a very long body but will not be truncated for the preview.',
            'user_id': str(self.user.id),
            'anonymous': False,
            'anonymous_to_peers': False,
        }
        self.check_mock_called_with("create_thread", -1, **params)
            

    def test_error(self):
        request_data = {
            "topic_id": "dummy",
            "type": "discussion",
            "title": "dummy",
            "raw_body": "dummy",
        }
        response = self.client.post(
            self.url,
            json.dumps(request_data),
            content_type="application/json"
        )
        expected_response_data = {
            "field_errors": {"course_id": {"developer_message": "This field is required."}}
        }
        assert response.status_code == 400
        response_data = json.loads(response.content.decode('utf-8'))
        assert response_data == expected_response_data

@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class ThreadViewSetRetrieveTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase, ProfileImageTestMixin):
    """Tests for ThreadViewSet Retrieve"""

    def setUp(self):
        super().setUp()
        self.url = reverse("thread-detail", kwargs={"thread_id": "test_thread"})
        self.thread_id = "test_thread"
        self.set_mock_return_value('get_course_id_by_comment', str(self.course.id))
        self.set_mock_return_value('get_course_id_by_thread', str(self.course.id))


    def test_basic(self):
        self.register_get_user_response(self.user)
        cs_thread = make_minimal_cs_thread({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "commentable_id": "test_topic",
            "username": self.user.username,
            "user_id": str(self.user.id),
            "title": "Test Title",
            "body": "Test body",
        })
        self.register_get_thread_response(cs_thread)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert json.loads(response.content.decode('utf-8')) == self.expected_thread_data({'unread_comment_count': 1})
        self.check_mock_called('get_thread')

    def test_retrieve_nonexistent_thread(self):
        self.register_get_thread_error_response(self.thread_id, 404)
        response = self.client.get(self.url)
        assert response.status_code == 404

    def test_profile_image_requested_field(self):
        """
        Tests thread has user profile image details if called in requested_fields
        """
        self.register_get_user_response(self.user)
        cs_thread = make_minimal_cs_thread({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "username": self.user.username,
            "user_id": str(self.user.id),
        })
        self.register_get_thread_response(cs_thread)
        self.create_profile_image(self.user, get_profile_image_storage())
        response = self.client.get(self.url, {"requested_fields": "profile_image"})
        assert response.status_code == 200
        expected_profile_data = self.get_expected_user_profile(self.user.username)
        response_users = json.loads(response.content.decode('utf-8'))['users']
        assert expected_profile_data == response_users[self.user.username]


@httpretty.activate
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetRetrieveTest(DiscussionAPIViewTestMixin, ModuleStoreTestCase, ProfileImageTestMixin):
    """Tests for CommentViewSet Retrieve"""

    def setUp(self):
        super().setUp()
        self.url = reverse("comment-detail", kwargs={"comment_id": "test_comment"})
        self.thread_id = "test_thread"
        self.comment_id = "test_comment"
        self.set_mock_return_value('get_course_id_by_comment', str(self.course.id))
        self.set_mock_return_value('get_course_id_by_thread', str(self.course.id))

    def make_comment_data(self, comment_id, parent_id=None, children=[]):  # pylint: disable=W0102
        """
        Returns comment dict object as returned by comments service
        """
        return make_minimal_cs_comment({
            "id": comment_id,
            "parent_id": parent_id,
            "course_id": str(self.course.id),
            "thread_id": self.thread_id,
            "thread_type": "discussion",
            "username": self.user.username,
            "user_id": str(self.user.id),
            "created_at": "2015-06-03T00:00:00Z",
            "updated_at": "2015-06-03T00:00:00Z",
            "body": "Original body",
            "children": children,
        })

    def test_basic(self):
        self.register_get_user_response(self.user)
        cs_comment_child = self.make_comment_data("test_child_comment", self.comment_id, children=[])
        cs_comment = self.make_comment_data(self.comment_id, None, [cs_comment_child])
        cs_thread = make_minimal_cs_thread({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "children": [cs_comment],
        })
        self.register_get_thread_response(cs_thread)
        self.register_get_comment_response(cs_comment)

        expected_response_data = {
            "id": "test_child_comment",
            "parent_id": self.comment_id,
            "thread_id": self.thread_id,
            "author": self.user.username,
            "author_label": None,
            "raw_body": "Original body",
            "rendered_body": "<p>Original body</p>",
            "created_at": "2015-06-03T00:00:00Z",
            "updated_at": "2015-06-03T00:00:00Z",
            "children": [],
            "endorsed_at": None,
            "endorsed": False,
            "endorsed_by": None,
            "endorsed_by_label": None,
            "voted": False,
            "vote_count": 0,
            "abuse_flagged": False,
            "abuse_flagged_any_user": None,
            "editable_fields": ["abuse_flagged", "anonymous", "raw_body"],
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

        response = self.client.get(self.url)
        assert response.status_code == 200
        assert json.loads(response.content.decode('utf-8'))['results'][0] == expected_response_data

    def test_retrieve_nonexistent_comment(self):
        self.register_get_comment_error_response(self.comment_id, 404)
        response = self.client.get(self.url)
        assert response.status_code == 404

    def test_pagination(self):
        """
        Test that pagination parameters are correctly plumbed through to the
        comments service and that a 404 is correctly returned if a page past the
        end is requested
        """
        self.register_get_user_response(self.user)
        cs_comment_child = self.make_comment_data("test_child_comment", self.comment_id, children=[])
        cs_comment = self.make_comment_data(self.comment_id, None, [cs_comment_child])
        cs_thread = make_minimal_cs_thread({
            "id": self.thread_id,
            "course_id": str(self.course.id),
            "children": [cs_comment],
        })
        self.register_get_thread_response(cs_thread)
        self.register_get_comment_response(cs_comment)
        response = self.client.get(
            self.url,
            {"comment_id": self.comment_id, "page": "18", "page_size": "4"}
        )
        self.assert_response_correct(
            response,
            404,
            {"developer_message": "Page not found (No results on this page)."}
        )

    def test_profile_image_requested_field(self):
        """
        Tests all comments retrieved have user profile image details if called in requested_fields
        """
        self.register_get_user_response(self.user)
        cs_comment_child = self.make_comment_data('test_child_comment', self.comment_id, children=[])
        cs_comment = self.make_comment_data(self.comment_id, None, [cs_comment_child])
        cs_thread = make_minimal_cs_thread({
            'id': self.thread_id,
            'course_id': str(self.course.id),
            'children': [cs_comment],
        })
        self.register_get_thread_response(cs_thread)
        self.register_get_comment_response(cs_comment)
        self.create_profile_image(self.user, get_profile_image_storage())

        response = self.client.get(self.url, {'requested_fields': 'profile_image'})
        assert response.status_code == 200
        response_comments = json.loads(response.content.decode('utf-8'))['results']

        for response_comment in response_comments:
            expected_profile_data = self.get_expected_user_profile(response_comment['author'])
            response_users = response_comment['users']
            assert expected_profile_data == response_users[response_comment['author']]

