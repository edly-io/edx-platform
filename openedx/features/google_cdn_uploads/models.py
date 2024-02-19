"""
Meta Translations Models
"""
from django.db import models
from opaque_keys.edx.django.models import CourseKeyField


class GoogleCDNUpload(models.Model):
    """
    Store course id and file name
    """
    course_id = CourseKeyField(max_length=255, db_index=True)
    file_name = models.CharField(max_length=255)

    class Meta:
        app_label = 'google_cdn_uploads'
        verbose_name = "Google CDN Uploads"
