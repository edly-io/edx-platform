"""
Admin registeration for colaraz_features app
"""
from django.contrib import admin

from openedx.features.colaraz_features.models import ColarazUserProfile


class ColarazUserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'site_identifier', 'level_percentage', 'level_text']

admin.site.register(ColarazUserProfile, ColarazUserProfileAdmin)
