"""Service layer for the video usage resources of a course."""

from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.video_storage_handlers import get_video_usage_path
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


def ensure_course_exists(course_key):
    """
    Raise ``NotFound`` unless a course run exists under ``course_key``.

    Only reads: the course overview table first, then the content store. The
    overview row is never created here, so a read request writes nothing.
    """
    if not CourseOverview.course_exists(course_key):
        raise NotFound("The course does not exist.")


def get_course_video_usages(course_key, edx_video_id):
    """
    Return where the video ``edx_video_id`` is placed in the course.

    The result is ``{"usage_locations": [{"display_location", "url"}, ...]}``,
    one entry per video component that references the id, in content-store
    order. Unpublished components are included; a component with no parent is
    left out. An id that nothing references gives an empty list.
    """
    ensure_course_exists(course_key)
    return get_video_usage_path(course_key, edx_video_id)
