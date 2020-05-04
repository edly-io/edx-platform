"""
Admin registeration for colaraz_features app
"""
from django.contrib import admin

from openedx.features.colaraz_features.models import ColarazUserProfile


class ColarazUserProfileAdmin(admin.ModelAdmin):
    list_display = [
        'user',
        'elgg_id',
        'job_title',
        'profile_image_url',
        'profile_strength_title',
        'profile_strength_color',
        'profile_strength_width',
        'site_identifier',
    ]

admin.site.register(ColarazUserProfile, ColarazUserProfileAdmin)
