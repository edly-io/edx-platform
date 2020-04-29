
"""
Django models for colaraz_features app.
"""
from __future__ import unicode_literals

from django.contrib.auth.models import User
from django.db import models


DEFAULT_JOB_TITLE = 'Student'
DEFAULT_PROFILE_STRENGTH_COLOR = '#A9A9A9' # gray
DEFAULT_PROFILE_STRENGTH_TITLE = 'Beginner'
DEFAULT_PROFILE_STRENGTH_WIDTH = '0%'


class ColarazUserProfile(models.Model):
    """
    Represents colaraz user profile.

    Fields:
        user: Linked auth User
        elgg_id: It will be used for api calls with colaraz
        job_title: User title to be shown on sidebar
        profile_image_url: User's profile image link
        profile_strength_title: Profile progress-bar title
        profile_strength_color: Profile progress-bar color
        profile_strength_width: Profile progress-bar width/percentage
        site_identifier: It represents the starting part of domain which identifies domain e.g: in
            'abc.courses.colaraz.com' the identifier will be 'abc'
    """
    class Meta:
        app_label = 'colaraz_features'
        verbose_name_plural = 'Colaraz user profiles'

    user = models.OneToOneField(User, unique=True, db_index=True,
                                related_name='colaraz_profile', on_delete=models.CASCADE)
    elgg_id = models.IntegerField(blank=True, null=True)
    job_title = models.CharField(max_length=100, default=DEFAULT_JOB_TITLE)
    profile_image_url = models.URLField(max_length=200, blank=True, null=True)
    profile_strength_title = models.CharField(max_length=100, default=DEFAULT_PROFILE_STRENGTH_TITLE)
    profile_strength_color = models.CharField(max_length=20, default=DEFAULT_PROFILE_STRENGTH_COLOR)
    profile_strength_width = models.CharField(max_length=5, default=DEFAULT_PROFILE_STRENGTH_WIDTH)
    site_identifier = models.CharField(max_length=100)
