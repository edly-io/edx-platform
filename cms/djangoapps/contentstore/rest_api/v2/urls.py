"""Contenstore API v2 URLs."""

from django.conf import settings
from django.urls import path, re_path
from rest_framework.routers import DefaultRouter

from cms.djangoapps.contentstore.rest_api.v2.views import downstreams, home, utils

app_name = "v2"

# ADR 0028: HomeCoursesViewSetV2 registered via DefaultRouter.
# Generates: GET home/courses/ → home-courses-list
router = DefaultRouter()
router.register(r'home/courses', home.HomeCoursesViewSetV2, basename='home-courses')

urlpatterns = router.urls + [
    # DEPRECATED (ADR 0028): kept for backward compatibility.
    # Will be removed after one named release.
    # Use GET home/courses/ (router URL name: home-courses-list) instead.
    path(
        "home/courses",
        home.HomePageCoursesViewV2.as_view(),
        name="courses",
    ),
    re_path(
        r'^downstreams/$',
        downstreams.DownstreamListView.as_view(),
        name="downstreams_list",
    ),
    re_path(
        fr'^downstreams/{settings.USAGE_KEY_PATTERN}$',
        downstreams.DownstreamView.as_view(),
        name="downstream"
    ),
    re_path(
        f'^downstreams/{settings.COURSE_KEY_PATTERN}/summary$',
        downstreams.DownstreamSummaryView.as_view(),
        name='upstream-summary-list'
    ),
    re_path(
        fr'^downstreams/{settings.USAGE_KEY_PATTERN}/sync$',
        downstreams.SyncFromUpstreamView.as_view(),
        name="sync_from_upstream"
    ),
    re_path(
        '^validate/numerical-input/$',
        utils.NumericalInputValidationView.as_view(),
        name='numerical_input_validation'),
]
