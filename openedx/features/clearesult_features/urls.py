"""
URLs for clearesult app.
"""
from django.conf.urls import url, include

from openedx.features.clearesult_features.views import (
    render_continuing_education, render_catalogs_manager,
    render_groups_manager, render_group_catalogs_manager,
    render_admin_configurations, render_reports, render_participation_code
)

from openedx.features.clearesult_features.authentication.views import SiteSecurityView


app_name = 'clearesult_features'

urlpatterns = (
    url(
        r'^continuing_education/$',
        render_continuing_education,
        name='continuing_education'
    ),
    url(
        r'^reports/$',
        render_reports,
        name='clearesult_reports'
    ),
    url(
        r'^site_security/$',
        SiteSecurityView.as_view(),
        name="site_security_code"
    ),
    url(
        r'^participation_code/$',
        render_participation_code,
        name="participation_code"
    ),
    url(
        r'^api/v0/',
        include('openedx.features.clearesult_features.api.v0.urls', namespace='api_v0')
    ),
    url(
        r'^catalogs_manager/$',
        render_catalogs_manager,
        name='catalogs_manager'
    ),
    url(
        r'^groups_manager/$',
        render_groups_manager,
        name='groups_manager'
    ),
    url(
        r'^group_catalogs_manager/$',
        render_group_catalogs_manager,
        name='groups_catalogs_manager'
    ),
    url(
        r'^admin_configurations/$',
        render_admin_configurations,
        name='admin_configurations'
    )
)
