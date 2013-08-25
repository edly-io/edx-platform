"""
Test for LMS courseware app.
"""
import mock
from django.core.urlresolvers import reverse
from django.test.utils import override_settings

from textwrap import dedent

from xmodule.error_module import ErrorDescriptor
from xmodule.modulestore.django import modulestore
from xmodule.modulestore import Location
from xmodule.modulestore.xml_importer import import_from_xml
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase

from courseware.tests.helpers import LoginEnrollmentTestCase
from courseware.tests.modulestore_config import TEST_DATA_DIR, \
    TEST_DATA_MONGO_MODULESTORE, \
    TEST_DATA_DRAFT_MONGO_MODULESTORE, \
    TEST_DATA_MIXED_MODULESTORE


class ActivateLoginTest(LoginEnrollmentTestCase):
    """
    Test logging in and logging out.
    """
    def setUp(self):
        self.setup_user()

    def test_activate_login(self):
        """
        Test login -- the setup function does all the work.
        """
        pass

    def test_logout(self):
        """
        Test logout -- setup function does login.
        """
        self.logout()


class PageLoaderTestCase(LoginEnrollmentTestCase):
    """
    Base class that adds a function to load all pages in a modulestore.
    """

    def check_all_pages_load(self, course_id):
        """
        Assert that all pages in the course load correctly.
        `course_id` is the ID of the course to check.
        """

        store = modulestore()

        # Enroll in the course before trying to access pages
        course = store.get_course(course_id)
        self.enroll(course, True)

        # Search for items in the course
        # None is treated as a wildcard
        course_loc = course.location
        location_query = Location(
            course_loc.tag, course_loc.org,
            course_loc.course, None, None, None
        )

        items = store.get_items(
            location_query,
            course_id=course_id,
            depth=2
        )

        if len(items) < 1:
            self.fail('Could not retrieve any items from course')

        # Try to load each item in the course
        for descriptor in items:

            if descriptor.location.category == 'about':
                self._assert_loads('about_course',
                                   {'course_id': course_id},
                                   descriptor)

            elif descriptor.location.category == 'static_tab':
                kwargs = {'course_id': course_id,
                          'tab_slug': descriptor.location.name}
                self._assert_loads('static_tab', kwargs, descriptor)

            elif descriptor.location.category == 'course_info':
                self._assert_loads('info', {'course_id': course_id},
                                   descriptor)

            else:

                kwargs = {'course_id': course_id,
                          'location': descriptor.location.url()}

                self._assert_loads('jump_to', kwargs, descriptor,
                                   expect_redirect=True,
                                   check_content=True)

    def _assert_loads(self, django_url, kwargs, descriptor,
                      expect_redirect=False,
                      check_content=False):
        """
        Assert that the url loads correctly.
        If expect_redirect, then also check that we were redirected.
        If check_content, then check that we don't get
        an error message about unavailable modules.
        """

        url = reverse(django_url, kwargs=kwargs)
        response = self.client.get(url, follow=True)

        if response.status_code != 200:
            self.fail('Status %d for page %s' %
                      (response.status_code, descriptor.location.url()))

        if expect_redirect:
            self.assertEqual(response.redirect_chain[0][1], 302)

        if check_content:
            self.assertNotContains(response, "this module is temporarily unavailable")
            self.assertNotIsInstance(descriptor, ErrorDescriptor)


@override_settings(MODULESTORE=TEST_DATA_MIXED_MODULESTORE)
class TestXmlCoursesLoad(ModuleStoreTestCase, PageLoaderTestCase):
    """
    Check that all pages in test courses load properly from XML.
    """

    def setUp(self):
        super(TestXmlCoursesLoad, self).setUp()
        self.setup_user()

    def test_toy_course_loads(self):

        # Load one of the XML based courses
        # Our test mapping rules allow the MixedModuleStore
        # to load this course from XML, not Mongo.
        self.check_all_pages_load('edX/toy/2012_Fall')


# Importing XML courses isn't possible with MixedModuleStore,
# so we use a Mongo modulestore directly (as we would in Studio)
@override_settings(MODULESTORE=TEST_DATA_MONGO_MODULESTORE)
class TestMongoCoursesLoad(ModuleStoreTestCase, PageLoaderTestCase):
    """
    Check that all pages in test courses load properly from Mongo.
    """

    def setUp(self):
        super(TestMongoCoursesLoad, self).setUp()
        self.setup_user()

        # Import the toy course into a Mongo-backed modulestore
        self.store = modulestore()
        import_from_xml(self.store, TEST_DATA_DIR, ['toy'])

    @mock.patch('xmodule.course_module.requests.get')
    def test_toy_textbooks_loads(self, mock_get):
        mock_get.return_value.text = dedent("""
            <?xml version="1.0"?><table_of_contents>
            <entry page="5" page_label="ii" name="Table of Contents"/>
            </table_of_contents>
        """).strip()

        location = Location(['i4x', 'edX', 'toy', 'course', '2012_Fall', None])
        course = self.store.get_item(location)
        self.assertGreater(len(course.textbooks), 0)


@override_settings(MODULESTORE=TEST_DATA_DRAFT_MONGO_MODULESTORE)
class TestDraftModuleStore(ModuleStoreTestCase):
    def test_get_items_with_course_items(self):
        store = modulestore()

        # fix was to allow get_items() to take the course_id parameter
        store.get_items(Location(None, None, 'vertical', None, None),
                        course_id='abc', depth=0)

        # test success is just getting through the above statement.
        # The bug was that 'course_id' argument was
        # not allowed to be passed in (i.e. was throwing exception)
