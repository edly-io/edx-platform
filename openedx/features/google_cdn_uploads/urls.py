"""
Urls for Meta Translations
"""
from django.conf.urls import include, url

from  openedx.features.google_cdn_uploads.views import render_google_cdn_uploads_home

app_name = 'google_cdn_uploads'

urlpatterns = [
    # url(
    #     r'^course_blocks_mapping/$',
    #     course_blocks_mapping,
    #     name='course_blocks_mapping'
    # ),
    url(
        r'^$',
        render_google_cdn_uploads_home,
        name='google_cdn_uploads_home'
    ),
]
