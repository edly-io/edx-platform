"""
URL patterns for colaraz features application.
"""
from django.conf import settings
from django.conf.urls import url

from openedx.features.colaraz_features.api.views import (
    SiteOrgViewSet,
    NotificationHandlerApiView,
    JobAlertsHandlerApiView,
    CourseOutlineView,
)

urlpatterns = [
    url(r'^site-org/', SiteOrgViewSet.as_view({'post': 'create'}), name='site-org'),
    url(r'^notifications/(?P<api_method>\D+)', NotificationHandlerApiView.as_view(), name="notifications_handler"),
    url(r'^job-alerts/(?P<api_method>\D+)', JobAlertsHandlerApiView.as_view(), name="job_alerts_handler"),
    url(r'^course/{}'.format(settings.COURSE_ID_PATTERN), CourseOutlineView.as_view(), name="course_outline"),
]
