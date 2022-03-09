from rest_framework import routers

from openedx.features.edly.api.v1.views.user_sites import UserSitesViewSet

from figures.views import CourseEnrollmentViewSet

router = routers.SimpleRouter()
router.register(r'user_sites', UserSitesViewSet, base_name='user_sites')
router.register(
    r'course_enrollment',
    CourseEnrollmentViewSet,
    base_name='course_enrollments'
    )

urlpatterns = router.urls
