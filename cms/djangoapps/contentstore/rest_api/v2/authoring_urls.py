"""Contentstore API v2 authoring URLs."""

from django.urls import path, re_path

from cms.djangoapps.contentstore.rest_api.v1.views.unknown_route import UnknownRouteView
from cms.djangoapps.contentstore.rest_api.v2.views.video_usages import CourseVideoUsageViewSet

app_name = "authoring_v2"

urlpatterns = [
    path(
        "courses/<course_key:course_key>/video_usages/<str:edx_video_id>/",
        CourseVideoUsageViewSet.as_view({"get": "retrieve"}),
        name="course_video_usage_detail",
    ),
    # Every other address under this collection holds nothing: a course key
    # the route above refuses (malformed, or in the deprecated slash-separated
    # form), the usage collection without a video id, an empty video id, or a
    # path below a usage. They answer with the API error body rather than the
    # site's HTML error page. They match addresses under this collection and
    # nothing else, so a resource added under courses/ is never shadowed, and
    # only slash-terminated ones, so the redirect to the slash-terminated form
    # still happens first. A path converter cannot match an empty segment,
    # hence the pattern for the addresses below a usage.
    path(
        "courses/<path:course_key>/video_usages/",
        UnknownRouteView.as_view(),
        name="course_video_usage_list_unmatched",
    ),
    re_path(
        r"^courses/(?P<course_key>.+)/video_usages/(?P<subpath>.*)/$",
        UnknownRouteView.as_view(),
        name="course_video_usage_detail_unmatched",
    ),
]
