"""
Views for uploading mp4
"""
import json
from logging import getLogger
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from xmodule.modulestore.django import modulestore
from opaque_keys.edx.keys import CourseKey
from django.conf import settings
from common.djangoapps.edxmako.shortcuts import render_to_response

from lms.djangoapps.courseware.courses import get_course_by_id
log = getLogger(__name__)


@login_required
def render_google_cdn_uploads_home(request):
    return render_to_response('uploads.html', {
        'uses_bootstrap': True,
        'login_user_username': request.user.username,
    })
