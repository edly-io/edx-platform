"""
URL patterns for colaraz features application.
"""

from django.conf.urls import url, include
from openedx.features.colaraz_features.api import urls


urlpatterns = [
    url(
        r'^v1/', include(urls.urlpatterns)
    ),
]
