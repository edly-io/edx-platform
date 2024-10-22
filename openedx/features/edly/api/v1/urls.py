from rest_framework import routers

from openedx.features.edly.api.v1.views.course_enrollments import EdlyCourseEnrollmentViewSet
from openedx.features.edly.api.v1.views.user_sites import UserSitesViewSet
from openedx.features.edly.api.v1.views.user_mutisites import MultisitesViewset
from openedx.features.edly.api.v1.views.enrollment_count import EdlyProgramEnrollmentCountViewSet


router = routers.SimpleRouter()
router.register(r'user_sites', UserSitesViewSet, base_name='user_sites')
router.register(r'user_link_sites', MultisitesViewset, base_name='mutisite_access')

router.register(
    r'courses/course_enrollment',
    EdlyCourseEnrollmentViewSet,
    base_name='course_enrollment',
)

router.register(
    r'programs/enrollment_count/(?P<program_uuid>[0-9a-f-]+)',
    EdlyProgramEnrollmentCountViewSet,
    base_name='program_enrollment_count',
)

urlpatterns = router.urls
