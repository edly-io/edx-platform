"""
Admin registration for Messenger.
"""
from django.contrib import admin

from openedx.features.google_cdn_uploads.models import GoogleCDNUpload


class GoogleCDNUploadAdmin(admin.ModelAdmin):
    """
    Admin config clearesult credit providers.
    """
    list_display  = ("file_name", "course_id",)
    search_fields = ("file_name", "course_id",)


admin.site.register(GoogleCDNUpload, GoogleCDNUploadAdmin)
