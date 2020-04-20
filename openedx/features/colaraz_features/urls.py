"""
URL patterns for colaraz features application.
"""

from django.conf.urls import url, include
from openedx.features.colaraz_features.api import urls
from openedx.features.colaraz_features.views import AuthProviderLogoutRedirectView

urlpatterns = [
    url(
        r'^v1/', include(urls.urlpatterns),
    ),
    url(
        r'^auth_logout_redirect/', AuthProviderLogoutRedirectView.as_view(), name='auth_logout_redirect'
    ),
]
