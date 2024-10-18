import pytest
# pylint: skip-file
"""Tests for django comment client views."""


import json
import logging
from contextlib import contextmanager
from unittest import mock
from unittest.mock import ANY, Mock, patch

import ddt
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test.client import RequestFactory
from django.urls import reverse
from eventtracking.processors.exceptions import EventEmissionExit
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator
from openedx_events.learning.signals import FORUM_THREAD_CREATED, FORUM_THREAD_RESPONSE_CREATED, FORUM_RESPONSE_COMMENT_CREATED

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.roles import CourseStaffRole, UserBasedRole
from common.djangoapps.student.tests.factories import CourseAccessRoleFactory, CourseEnrollmentFactory, UserFactory
from common.djangoapps.track.middleware import TrackMiddleware
from common.djangoapps.track.views import segmentio
from common.djangoapps.track.views.tests.base import SEGMENTIO_TEST_USER_ID, SegmentIOTrackingTestCaseBase
from common.djangoapps.util.testing import UrlResetMixin
from common.test.utils import MockSignalHandlerMixin, disable_signal
from lms.djangoapps.discussion.django_comment_client.base import views
from lms.djangoapps.discussion.django_comment_client.tests.group_id_v2 import (
    CohortedTopicGroupIdTestMixin,
    GroupIdAssertionMixin,
    NonCohortedTopicGroupIdTestMixin
)
from lms.djangoapps.discussion.django_comment_client.tests.unicode import UnicodeTestMixin
from lms.djangoapps.discussion.django_comment_client.tests.utils import CohortedTestCase, ForumsEnableMixin
from lms.djangoapps.teams.tests.factories import CourseTeamFactory, CourseTeamMembershipFactory
from openedx.core.djangoapps.course_groups.cohorts import set_course_cohorted
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.django_comment_common.comment_client import Thread
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_STUDENT,
    CourseDiscussionSettings,
    Role,
    assign_role
)
from openedx.core.djangoapps.django_comment_common.utils import (
    ThreadContext,
    seed_permissions_roles,
)
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from openedx.core.lib.teams_config import TeamsConfig
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase, SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory, check_mongo_calls

from .event_transformers import ForumThreadViewedEventTransformer

log = logging.getLogger(__name__)

QUERY_COUNT_TABLE_IGNORELIST = WAFFLE_TABLES

CS_PREFIX = "http://localhost:4567/api/v1"

# pylint: disable=missing-docstring


@patch('lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled', return_value=True)
@patch('openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_thread', autospec=True)
class CreateThreadGroupIdTestCase(
        CohortedTestCase,
        CohortedTopicGroupIdTestMixin,
        NonCohortedTopicGroupIdTestMixin
):
    cs_endpoint = "/threads"

    def call_view(self, mock_create_thread, mock_is_forum_v2_enabled, commentable_id, user, group_id, pass_group_id=True):
        mock_create_thread.return_value = {}
        request_data = {"body": "body", "title": "title", "thread_type": "discussion"}
        if pass_group_id:
            request_data["group_id"] = group_id
        request = RequestFactory().post("dummy_url", request_data)
        request.user = user
        request.view_name = "create_thread"

        return views.create_thread(
            request,
            course_id=str(self.course.id),
            commentable_id=commentable_id
        )

    def test_group_info_in_response(self, mock_is_forum_v2_enabled, mock_request):
        response = self.call_view(
            mock_is_forum_v2_enabled,
            mock_request,
            "cohorted_topic",
            self.student,
            ''
        )
        self._assert_json_response_contains_group_info(response)

@patch('lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled', return_value=True)
@disable_signal(views, 'thread_edited')
@disable_signal(views, 'thread_voted')
@disable_signal(views, 'thread_deleted')
class ThreadActionGroupIdTestCase(
        CohortedTestCase,
        GroupIdAssertionMixin
):
    
    def _get_mocked_instance_from_view_name(self, view_name):
        """
        Get the relavent Mock function based on the view_name
        """
        mocks = {
            "create_thread": self.mock_create_thread,
            "get_thread": self.mock_get_thread,
            "update_thread": self.mock_update_thread,
            "delete_thread": self.mock_delete_thread,
            "vote_for_thread": self.mock_update_thread_votes,
        }
        return mocks.get(view_name)
    
    def setUp(self):
        super().setUp()
        # Mocking create_thread and get_thread methods
        self.mock_create_thread = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_thread', autospec=True).start()
        self.mock_get_thread = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread', autospec=True).start()
        self.mock_update_thread = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_thread', autospec=True).start()
        self.mock_delete_thread = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.delete_thread', autospec=True).start()
        self.mock_update_thread_votes = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.update_thread_votes', autospec=True).start()
        self.mock_delete_thread_vote = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.user.forum_api.delete_thread_vote', autospec=True).start()
        self.mock_update_thread_flag = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.update_thread_flag', autospec=True).start()
        self.mock_pin_thread = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.pin_thread', autospec=True).start()
        self.mock_unpin_thread = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.unpin_thread', autospec=True).start()



        default_response = {
            "user_id": str(self.student.id),
            "group_id": self.student_cohort.id,
            "closed": False,
            "type": "thread",
            "commentable_id": "non_team_dummy_id",
            "body": "test body",
        }
        self.mock_create_thread.return_value = default_response
        self.mock_get_thread.return_value = default_response
        self.mock_update_thread.return_value = default_response
        self.mock_delete_thread.return_value = default_response
        self.mock_update_thread_votes.return_value = default_response
        self.mock_delete_thread_vote.return_value = default_response
        self.mock_update_thread_flag.return_value = default_response
        self.mock_pin_thread.return_value = default_response
        self.mock_unpin_thread.return_value = default_response
        
        self.get_course_id_by_thread = mock.patch('openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread', autospec=True).start()
        self.get_course_id_by_thread.return_value = CourseLocator('dummy', 'test_123', 'test_run')
        
        self.addCleanup(mock.patch.stopall)  # Ensure all mocks are stopped after tests
    

    def call_view(
            self,
            view_name,
            mock_is_forum_v2_enabled,
            user=None,
            post_params=None,
            view_args=None
    ):
        mocked_view = self._get_mocked_instance_from_view_name(view_name)
        if mocked_view:
            mocked_view.return_value = {
                "user_id": str(self.student.id),
                "group_id": self.student_cohort.id,
                "closed": False,
                "type": "thread",
                "commentable_id": "non_team_dummy_id",
                "body": "test body",
            }
        request = RequestFactory().post("dummy_url", post_params or {})
        request.user = user or self.student
        request.view_name = view_name

        return getattr(views, view_name)(
            request,
            course_id=str(self.course.id),
            thread_id="dummy",
            **(view_args or {})
        )

    def test_update(self, mock_is_forum_v2_enabled):
        response = self.call_view(
            "update_thread",
            mock_is_forum_v2_enabled,
            post_params={"body": "body", "title": "title"}
        )
        self._assert_json_response_contains_group_info(response)

    def test_delete(self, mock_is_forum_v2_enabled):
        response = self.call_view("delete_thread", mock_is_forum_v2_enabled)
        self._assert_json_response_contains_group_info(response)

    def test_vote(self, mock_is_forum_v2_enabled):
        response = self.call_view(
            "vote_for_thread",
            mock_is_forum_v2_enabled,
            view_args={"value": "up"}
        )
        self._assert_json_response_contains_group_info(response)
        response = self.call_view("undo_vote_for_thread", mock_is_forum_v2_enabled)
        self._assert_json_response_contains_group_info(response)

    def test_flag(self, mock_is_forum_v2_enabled):
        with mock.patch('openedx.core.djangoapps.django_comment_common.signals.thread_flagged.send') as signal_mock:
            response = self.call_view("flag_abuse_for_thread", mock_is_forum_v2_enabled)
            self._assert_json_response_contains_group_info(response)
            self.assertEqual(signal_mock.call_count, 1)
        response = self.call_view("un_flag_abuse_for_thread", mock_is_forum_v2_enabled)
        self._assert_json_response_contains_group_info(response)

    def test_pin(self, mock_is_forum_v2_enabled):
        response = self.call_view(
            "pin_thread",
            mock_is_forum_v2_enabled,
            user=self.moderator
        )
        self._assert_json_response_contains_group_info(response)
        response = self.call_view(
            "un_pin_thread",
            mock_is_forum_v2_enabled,
            user=self.moderator
        )
        self._assert_json_response_contains_group_info(response)

    def test_openclose(self, mock_is_forum_v2_enabled):
        response = self.call_view(
            "openclose_thread",
            mock_is_forum_v2_enabled,
            user=self.moderator
        )
        self._assert_json_response_contains_group_info(
            response,
            lambda d: d['content']
        )

class ViewsTestCaseMixin:

    def set_up_course(self, block_count=0):
        """
        Creates a course, optionally with block_count discussion blocks, and
        a user with appropriate permissions.
        """

        # create a course
        self.course = CourseFactory.create(
            org='MITx', course='999',
            discussion_topics={"Some Topic": {"id": "some_topic"}},
            display_name='Robot Super Course',
        )
        self.course_id = self.course.id

        # add some discussion blocks
        for i in range(block_count):
            BlockFactory.create(
                parent_location=self.course.location,
                category='discussion',
                discussion_id=f'id_module_{i}',
                discussion_category=f'Category {i}',
                discussion_target=f'Discussion {i}'
            )

        # seed the forums permissions and roles
        call_command('seed_permissions_roles', str(self.course_id))

        # Patch the comment client user save method so it does not try
        # to create a new cc user when creating a django user
        with patch('common.djangoapps.student.models.user.cc.User.save'):
            uname = 'student'
            email = 'student@edx.org'
            self.password = 'Password1234'

            # Create the user and make them active so we can log them in.
            self.student = UserFactory.create(username=uname, email=email, password=self.password)
            self.student.is_active = True
            self.student.save()

            # Add a discussion moderator
            self.moderator = UserFactory.create(password=self.password)

            # Enroll the student in the course
            CourseEnrollmentFactory(user=self.student,
                                    course_id=self.course_id)

            # Enroll the moderator and give them the appropriate roles
            CourseEnrollmentFactory(user=self.moderator, course_id=self.course.id)
            self.moderator.roles.add(Role.objects.get(name="Moderator", course_id=self.course.id))

            assert self.client.login(username='student', password=self.password)


    def _get_mocked_dict(self):
        return {
            "create_thread": self.mock_create_thread,
            "get_thread": self.mock_get_thread,
            "update_thread": self.mock_update_thread
        }

    def _get_mocked_instance_from_view_name(self, view_name):
        """
        Get the relavent Mock function based on the view_name
        """
        return self._get_mocked_dict().get(view_name)
    

    def _setup_mock_data(self, view_name="get_thread", include_depth=False):
        """
        Ensure that mock_request returns the data necessary to make views
        function correctly
        """
        data = {
            "user_id": str(self.student.id),
            "closed": False,
            "commentable_id": "non_team_dummy_id",
            "thread_id": "dummy",
            "thread_type": "discussion"
        }
        if include_depth:
            data["depth"] = 0
        self._get_mocked_instance_from_view_name(view_name).return_value = data

    def create_thread_helper(self, mock_is_forum_v2_enabled, extra_request_data=None, extra_response_data=None):
        """
        Issues a request to create a thread and verifies the result.
        """
        self.mock_create_thread.return_value = {
            "thread_type": "discussion",
            "title": "Hello",
            "body": "this is a post",
            "course_id": "MITx/999/Robot_Super_Course",
            "anonymous": False,
            "anonymous_to_peers": False,
            "commentable_id": "i4x-MITx-999-course-Robot_Super_Course",
            "created_at": "2013-05-10T18:53:43Z",
            "updated_at": "2013-05-10T18:53:43Z",
            "at_position_list": [],
            "closed": False,
            "id": "518d4237b023791dca00000d",
            "user_id": "1",
            "username": "robot",
            "votes": {
                "count": 0,
                "up_count": 0,
                "down_count": 0,
                "point": 0
            },
            "abuse_flaggers": [],
            "type": "thread",
            "group_id": None,
            "pinned": False,
            "endorsed": False,
            "unread_comments_count": 0,
            "read": False,
            "comments_count": 0,
        }
        thread = {
            "thread_type": "discussion",
            "body": ["this is a post"],
            "anonymous_to_peers": ["false"],
            "auto_subscribe": ["false"],
            "anonymous": ["false"],
            "title": ["Hello"],
        }
        if extra_request_data:
            thread.update(extra_request_data)
        url = reverse('create_thread', kwargs={'commentable_id': 'i4x-MITx-999-course-Robot_Super_Course',
                                               'course_id': str(self.course_id)})
        response = self.client.post(url, data=thread)
        assert self.mock_create_thread.called
        expected_data = {
            'thread_type': 'discussion',
            'body': 'this is a post',
            'context': ThreadContext.COURSE,
            'anonymous_to_peers': False,
            'user_id': '1',
            'title': 'Hello',
            'commentable_id': 'i4x-MITx-999-course-Robot_Super_Course',
            'anonymous': False,
            'course_id': str(self.course_id),
        }
        if extra_response_data:
            expected_data.update(extra_response_data)
        
        self.mock_create_thread.assert_called_with(**expected_data)
        assert response.status_code == 200


    def update_thread_helper(self, mock_is_forum_v2_enabled):
        """
        Issues a request to update a thread and verifies the result.
        """
        self._setup_mock_data("get_thread")
        self._setup_mock_data("update_thread")
        # Mock out saving in order to test that content is correctly
        # updated. Otherwise, the call to thread.save() receives the
        # same mocked request data that the original call to retrieve
        # the thread did, overwriting any changes.
        with patch.object(Thread, 'save'):
            response = self.client.post(
                reverse("update_thread", kwargs={
                    "thread_id": "dummy",
                    "course_id": str(self.course_id)
                }),
                data={"body": "foo", "title": "foo", "commentable_id": "some_topic"}
            )
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert data['body'] == 'foo'
        assert data['title'] == 'foo'
        assert data['commentable_id'] == 'some_topic'


@ddt.ddt
@patch('lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled', return_value=True)
@disable_signal(views, 'thread_created')
@disable_signal(views, 'thread_edited')
class ViewsQueryCountTestCase(
        ForumsEnableMixin,
        UrlResetMixin,
        ModuleStoreTestCase,
        ViewsTestCaseMixin
):

    CREATE_USER = False
    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']
    ENABLED_SIGNALS = ['course_published']


    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()
        self.mock_create_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_thread', autospec=True
        ).start()
        self.mock_update_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_thread', autospec=True
        ).start()
        self.mock_get_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread', autospec=True
        ).start()

        self.get_course_id_by_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread', autospec=True
        ).start()
        self.get_course_id_by_thread.return_value = CourseLocator('MITx', '999', 'Robot_Super_Course')

        self.addCleanup(mock.patch.stopall)

    def count_queries(func):  # pylint: disable=no-self-argument
        """
        Decorates test methods to count mongo and SQL calls for a
        particular modulestore.
        """

        def inner(self, default_store, block_count, mongo_calls, sql_queries, *args, **kwargs):
            with modulestore().default_store(default_store):
                self.set_up_course(block_count=block_count)
                self.clear_caches()
                with self.assertNumQueries(sql_queries, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST):
                    with check_mongo_calls(mongo_calls):
                        func(self, *args, **kwargs)
        return inner

    @ddt.data(
        (ModuleStoreEnum.Type.split, 3, 8, 41),
    )
    @ddt.unpack
    @count_queries
    def test_create_thread(self, mock_is_forum_v2_enabled):
        self.create_thread_helper(mock_is_forum_v2_enabled)

    @ddt.data(
        (ModuleStoreEnum.Type.split, 3, 6, 40),
    )
    @ddt.unpack
    @count_queries
    def test_update_thread(self, mock_is_forum_v2_enabled):
        self.update_thread_helper(mock_is_forum_v2_enabled)


@ddt.ddt
@disable_signal(views, 'comment_flagged')
@disable_signal(views, 'thread_flagged')
@patch('lms.djangoapps.discussion.toggles.ENABLE_FORUM_V2.is_enabled', autospec=True)
class ViewsTestCase(
        ForumsEnableMixin,
        UrlResetMixin,
        SharedModuleStoreTestCase,
        ViewsTestCaseMixin,
        MockSignalHandlerMixin
):

    def _get_mocked_dict(self):
        mocked_dict = super()._get_mocked_dict()
        mocked_dict['create_comment'] = self.mock_create_parent_comment
        return mocked_dict

    @classmethod
    def setUpClass(cls):
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            cls.course = CourseFactory.create(
                org='MITx', course='999',
                discussion_topics={"Some Topic": {"id": "some_topic"}},
                display_name='Robot Super Course',
            )

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.course_id = cls.course.id

        # seed the forums permissions and roles
        call_command('seed_permissions_roles', str(cls.course_id))
    
    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        # Patching the ENABLE_DISCUSSION_SERVICE value affects the contents of urls.py,
        # so we need to call super.setUp() which reloads urls.py (because
        # of the UrlResetMixin)
        super().setUp()
    
        # Patch the comment client user save method so it does not try
        # to create a new cc user when creating a django user
        with patch('common.djangoapps.student.models.user.cc.User.save'):
            uname = 'student'
            email = 'student@edx.org'
            self.password = 'Password1234'

            # Create the user and make them active so we can log them in.
            self.student = UserFactory.create(username=uname, email=email, password=self.password)
            self.student.is_active = True
            self.student.save()

            # Add a discussion moderator
            self.moderator = UserFactory.create(password=self.password)

            # Enroll the student in the course
            CourseEnrollmentFactory(user=self.student,
                                    course_id=self.course_id)

            # Enroll the moderator and give them the appropriate roles
            CourseEnrollmentFactory(user=self.moderator, course_id=self.course.id)
            self.moderator.roles.add(Role.objects.get(name="Moderator", course_id=self.course.id))

            assert self.client.login(username='student', password=self.password)


        self.mock_create_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_thread', autospec=True
        ).start()
        self.mock_update_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_thread', autospec=True
        ).start()
        self.mock_get_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_thread', autospec=True
        ).start()
        self.mock_create_subscription = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.create_subscription', autospec=True
        ).start()
        self.mock_delete_subscription = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.delete_subscription', autospec=True
        ).start()
        self.mock_delete_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.delete_thread', autospec=True
        ).start()
        self.mock_delete_comment = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.delete_comment', autospec=True
        ).start()
        self.mock_get_parent_comment = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.get_parent_comment', autospec=True
        ).start()
        self.mock_create_parent_comment = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.create_parent_comment', autospec=True
        ).start()
        self.mock_update_comment = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.models.forum_api.update_comment', autospec=True
        ).start()

        default_response = {
            "user_id": str(self.student.id),
            "closed": False,
            "type": "thread",
            "commentable_id": "non_team_dummy_id",
            "body": "test body",
        }
        self.mock_create_thread.return_value = default_response
        self.mock_get_thread.return_value = default_response
        self.mock_update_thread.return_value = default_response
        self.mock_delete_thread.return_value = default_response
        self.mock_delete_subscription.return_value = default_response
        self.mock_get_parent_comment.return_value = default_response

        self.get_course_id_by_thread = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_thread', autospec=True
        ).start()
        self.get_course_id_by_thread.return_value = CourseLocator('MITx', '999', 'Robot_Super_Course')

        self.get_course_id_by_comment = mock.patch(
            'openedx.core.djangoapps.django_comment_common.comment_client.thread.forum_api.get_course_id_by_comment', autospec=True
        ).start()
        self.get_course_id_by_comment.return_value = CourseLocator('MITx', '999', 'Robot_Super_Course')
        # forum_api.create_subscription

        self.addCleanup(mock.patch.stopall)
        

    @contextmanager
    def assert_discussion_signals(self, signal, user=None):
        if user is None:
            user = self.student
        with self.assert_signal_sent(views, signal, sender=None, user=user, exclude_args=('post',)):
            yield

    def test_create_thread(self, mock_is_forum_v2_enabled,):
        with self.assert_discussion_signals('thread_created'):
            self.create_thread_helper(mock_is_forum_v2_enabled)

    def test_create_thread_standalone(self, mock_is_forum_v2_enabled):
        team = CourseTeamFactory.create(
            name="A Team",
            course_id=self.course_id,
            topic_id='topic_id',
            discussion_topic_id="i4x-MITx-999-course-Robot_Super_Course"
        )

        # Add the student to the team so they can post to the commentable.
        team.add_user(self.student)

        # create_thread_helper verifies that extra data are passed through to the comments service
        self.create_thread_helper(mock_is_forum_v2_enabled, extra_response_data={'context': ThreadContext.STANDALONE})


    @ddt.data(
        ('follow_thread', 'thread_followed'),
        ('unfollow_thread', 'thread_unfollowed'),
    )
    @ddt.unpack
    def test_follow_unfollow_thread_signals(self, view_name, signal, mock_is_forum_v2_enabled):
        self.create_thread_helper(mock_is_forum_v2_enabled)
        with self.assert_discussion_signals(signal):
            response = self.client.post(
                reverse(
                    view_name,
                    kwargs={"course_id": str(self.course_id), "thread_id": 'i4x-MITx-999-course-Robot_Super_Course'}
                ),
                data = {}
            )
        assert response.status_code == 200

    def test_delete_thread(self, mock_is_forum_v2_enabled):
        self.mock_delete_thread.return_value = {
            "user_id": str(self.student.id),
            "closed": False,
            "body": "test body",
        }
        test_thread_id = "test_thread_id"
        request = RequestFactory().post("dummy_url", {"id": test_thread_id})
        request.user = self.student
        request.view_name = "delete_thread"
        with self.assert_discussion_signals('thread_deleted'):
            response = views.delete_thread(
                request,
                course_id=str(self.course.id),
                thread_id=test_thread_id
            )
        assert response.status_code == 200
        assert self.mock_delete_thread.called


    def test_delete_comment(self, mock_is_forum_v2_enabled):
        self.mock_delete_comment.return_value = {
            "user_id": str(self.student.id),
            "closed": False,
            "body": "test body",
        }
        test_comment_id = "test_comment_id"
        request = RequestFactory().post("dummy_url", {"id": test_comment_id})
        request.user = self.student
        request.view_name = "delete_comment"
        with self.assert_discussion_signals('comment_deleted'):
            response = views.delete_comment(
                request,
                course_id=str(self.course.id),
                comment_id=test_comment_id
            )
        assert response.status_code == 200
        assert self.mock_delete_comment.called

    def _test_request_error(self, view_name, view_kwargs, data):
        """
        Submit a request against the given view with the given data and ensure
        that the result is a 400 error and that no data was posted using
        mock_request
        """
        mocked_view = self._get_mocked_instance_from_view_name(view_name)
        if mocked_view:
            mocked_view.return_value = {}

        response = self.client.post(reverse(view_name, kwargs=view_kwargs), data=data)
        assert response.status_code == 400

    def test_create_thread_no_title(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "create_thread",
            {"commentable_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo"},
        )


    def test_create_thread_empty_title(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "create_thread",
            {"commentable_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo", "title": " "},
        )

    def test_create_thread_no_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "create_thread",
            {"commentable_id": "dummy", "course_id": str(self.course_id)},
            {"title": "foo"},
        )

    def test_create_thread_empty_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "create_thread",
            {"commentable_id": "dummy", "course_id": str(self.course_id)},
            {"body": " ", "title": "foo"}
        )

    def test_update_thread_no_title(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo"}
        )

    def test_update_thread_empty_title(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo", "title": " "}
        )

    def test_update_thread_no_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"title": "foo"}
        )

    def test_update_thread_empty_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": " ", "title": "foo"}
        )

    def test_update_thread_course_topic(self, mock_is_forum_v2_enabled):
        with self.assert_discussion_signals('thread_edited'):
            self.update_thread_helper(mock_is_forum_v2_enabled)

    @patch(
        'lms.djangoapps.discussion.django_comment_client.utils.get_discussion_categories_ids',
        return_value=["test_commentable"],
    )
    def test_update_thread_wrong_commentable_id(self, mock_get_discussion_id_map, mock_is_forum_v2_enabled):
        self._test_request_error(
            "update_thread",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": "foo", "title": "foo", "commentable_id": "wrong_commentable"},
        )

    def test_create_comment(self, mock_is_forum_v2_enabled):
        self.mock_create_parent_comment = {}
        
        with self.assert_discussion_signals('comment_created'):
            response = self.client.post(
                reverse(
                    "create_comment",
                    kwargs={"course_id": str(self.course_id), "thread_id": "dummy"}
                ),
                data={"body": "body"}
            )
        assert response.status_code == 200

    def test_create_comment_no_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "create_comment",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {},
        )

    def test_create_comment_empty_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "create_comment",
            {"thread_id": "dummy", "course_id": str(self.course_id)},
            {"body": " "},
        )

    def test_create_sub_comment_no_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "create_sub_comment",
            {"comment_id": "dummy", "course_id": str(self.course_id)},
            {},
        )

    def test_create_sub_comment_empty_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "create_sub_comment",
            {"comment_id": "dummy", "course_id": str(self.course_id)},
            {"body": " "}
        )

    def test_update_comment_no_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "update_comment",
            {"comment_id": "dummy", "course_id": str(self.course_id)},
            {}
        )

    def test_update_comment_empty_body(self, mock_is_forum_v2_enabled):
        self._test_request_error(
            "update_comment",
            {"comment_id": "dummy", "course_id": str(self.course_id)},
            {"body": " "}
        )

    def test_update_comment_basic(self, mock_is_forum_v2_enabled):
        self.mock_update_comment.return_value = {}
        comment_id = "test_comment_id"
        updated_body = "updated body"
        with self.assert_discussion_signals('comment_edited'):
            response = self.client.post(
                reverse(
                    "update_comment",
                    kwargs={"course_id": str(self.course_id), "comment_id": comment_id}
                ),
                data={"body": updated_body}
            )
        assert response.status_code == 200
        assert self.mock_update_comment.call_args[1].get('body') == updated_body
