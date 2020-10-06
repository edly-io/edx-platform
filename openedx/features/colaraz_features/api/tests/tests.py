from django.urls import reverse
from rest_framework import status

from student.models import CourseEnrollment
from student.tests.factories import UserFactory
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory

TEST_PASSWORD = 'test'


class CourseOutlineViewTests(ModuleStoreTestCase):

    def setUp(self):
        super(CourseOutlineViewTests, self).setUp()
        with self.store.default_store(ModuleStoreEnum.Type.split):
            self.course = CourseFactory.create()
            with self.store.bulk_operations(self.course.id):
                # Create a basic course structure
                chapter = ItemFactory.create(category='chapter', parent_location=self.course.location)
                section = ItemFactory.create(category='sequential', parent_location=chapter.location)
                ItemFactory.create(category='vertical', parent_location=section.location)
        self.user = UserFactory(password=TEST_PASSWORD)
        CourseEnrollment.enroll(self.user, self.course.id)
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

    def test_return_response_successfully(self):
        url = reverse('colaraz:course_outline', kwargs={'course_id': unicode(self.course.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_course_not_found(self):
        # test course not found by passing invalid course id
        url = reverse('colaraz:course_outline', kwargs={'course_id': 'course-v10:uet+CS041+3045_T20'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_for_unauthenticated_user_return_error(self):
        url = reverse('colaraz:course_outline', kwargs={'course_id': unicode(self.course.id)})

        self.client.logout()

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
