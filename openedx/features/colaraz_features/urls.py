"""
URL patterns for colaraz features application.
"""

from django.conf.urls import url, include
from openedx.features.colaraz_features.api import urls
from openedx.features.colaraz_features import views


urlpatterns = [
    url(r'^api/v1/', include(urls.urlpatterns)),
    url(r'^api/v1/users', views.UserListView.as_view(), name='user-list-api-view'),
    url(r'^auth_logout_redirect/$', views.AuthProviderLogoutRedirectView.as_view(), name='auth_logout_redirect'),
    url(r'^course-access-roles/$', views.CourseAccessRoleListView.as_view(), name='course-access-roles-list'),
    url(
        r'^course-access-roles/create/$',
        views.CourseAccessRoleCreateView.as_view(),
        name='course-access-roles-create'
    ),
    url(
        r'^course-access-roles/(?P<pk>\d+)/edit/$',
        views.CourseAccessRoleUpdateView.as_view(),
        name='course-access-roles-update'
    ),
    url(
        r'^course-access-roles/(?P<pk>(\d+,?)+)/delete/$',
        views.CourseAccessRoleDeleteView.as_view(),
        name='course-access-roles-delete'
    ),
]
