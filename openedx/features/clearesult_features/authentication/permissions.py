"""
Permissions for authentication related views.
"""
import logging
from django.db.models import Q
from django.urls import reverse
from django.shortcuts import redirect
from django.http import HttpResponseForbidden

from openedx.features.clearesult_features.authentication.utils import is_user_authenticated_for_site
from openedx.features.clearesult_features.api.v0.validators import validate_sites_for_local_admin
from openedx.features.clearesult_features.utils import (
    get_groups_courses_generator,
    get_site_linked_courses_and_groups,
    get_user_all_courses
)
from openedx.features.clearesult_features.models import ClearesultGroupLinkage, ClearesultCourse

log = logging.getLogger(__name__)


def non_site_authenticated_user_required(view_fn):
    """
    Ensures that only a user that is not authenticated for current site
    can access the view.
    """

    def inner(_self, request, *args, **kwargs):
        if is_user_authenticated_for_site(request):
            return redirect(reverse('dashboard'))
        return view_fn(_self, request, *args, **kwargs)

    return inner


def local_admin_required(view_fn):
    """
    Ensures that only a user that is not authenticated for current site
    can access the view.
    """

    def inner(request, *args, **kwargs):
        error_response, allowed_sites = validate_sites_for_local_admin(request.user)

        # neither a super user nor local admin
        if error_response:
            return HttpResponseForbidden()

        return view_fn(request, *args, **kwargs)

    return inner


def course_linked_user_required(view_fn):
    """
    Decorator only for course URLS
    Ensures that user should only access courses accessible to their groups.
    """

    def inner(request, *args, **kwargs):
        log.info("course_linked_user_required - Decorator called to check if course is accessible for the users")
        course_key = kwargs.get('course_key_string') or kwargs.get('course_id')

        if not course_key and "courseware" in request.path:
            # for courseware url, we have to extract course id from url as we are not getting it in kwargs
            url_split = request.path.split('/')
            if len(url_split) > 2:
                course_key = url_split[2]

        log.info("course_linked_user_required - course_key: {} and request.path: {}".format(course_key, request.path))

        if not request.user or request.user.is_superuser or request.user.is_staff:
            # for super user and staff users, run normal flow.
            # no restriction for superusers
            log.info("course_linked_user_required - Super User flow")
            return view_fn(request, *args, **kwargs)

        if request.user.is_anonymous:
            # anonymous user flow
            # un-authenticated user can view all courses linked to any group of the site
            log.info("course_linked_user_required - Anonymous User flow")
            accessble_courses, _ = get_site_linked_courses_and_groups([request.site])
        else:
            error, allowed_sites = validate_sites_for_local_admin(request.user)
            if allowed_sites:
                log.info("course_linked_user_required - Local Admin User flow")
                # local admin flow
                # local admin will have access to all the public and private courses of accesible sites
                accessble_courses = ClearesultCourse.objects.filter(Q(site__in=allowed_sites) | Q(site=None))
            else:
                log.info("course_linked_user_required - Authenticated User flow")
                # normal user flow
                # normal authenticated user can only view courses linked to his group
                accessble_courses = get_user_all_courses(request.user)

        if accessble_courses.filter(course_id=course_key).exists():
            return view_fn(request, *args, **kwargs)

        log.error("course_linked_user_required - BLOCK USER {}, on course: {} for path: {}".format(
            "Anonymous" if request.user.is_anonymous else request.user.email,
            course_key,
            request.path
        ))
        return HttpResponseForbidden()
    return inner
