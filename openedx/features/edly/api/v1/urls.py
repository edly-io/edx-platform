from rest_framework import routers

from openedx.features.edly.api.v1.views.course_enrollments import EdlyCourseEnrollmentViewSet
from openedx.features.edly.api.v1.views.enrollment_count import EdlyProgramEnrollmentCountViewSet
from openedx.features.edly.api.v1.views.user_mutisites import MultisitesViewset
from openedx.features.edly.api.v1.views.user_paid_for_course import UserPaidForCourseViewSet
from openedx.features.edly.api.v1.views.user_sites import UserSitesViewSet

router = routers.SimpleRouter()
router.register(r'user_sites', UserSitesViewSet, base_name='user_sites')
router.register(r'user_link_sites', MultisitesViewset, base_name='mutisite_access')

router.register(
    r'courses/course_enrollment',
    EdlyCourseEnrollmentViewSet,
    base_name='course_enrollment',
)

router.register(
    r'programs/enrollment_count',
    EdlyProgramEnrollmentCountViewSet,
    base_name='program_enrollment_count',
)

router.register(
    r'user_paid_for_course',
    UserPaidForCourseViewSet,
    base_name='user_paid_for_course'
)

urlpatterns = router.urls
