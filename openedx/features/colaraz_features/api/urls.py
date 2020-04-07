"""
URL patterns for colaraz features application.
"""
from django.conf.urls import url

from openedx.features.colaraz_features.api.views import SiteOrgViewSet

urlpatterns = [
    url(r'^site-org/', SiteOrgViewSet.as_view({'post': 'create'}))
]
