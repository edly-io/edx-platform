"""
URLs for clearesult API v0.
"""
from django.conf.urls import url

from openedx.features.clearesult_features.api.v0.views import (
    ClearesultCreditProviderListView, UserCreditProfileViewset,
    ClearesultCatalogViewset, ClearesultCourseViewset, SiteViewset,
    ClearesultGroupViewset, ClearesultGroupCatalogsViewset,
    SiteLinkedObjectsListView, ClearesultUpdateGroupCatalogsViewset,
    ClearesultMandatoryCoursesViewset, ClearesultLogoutView, retake_course,
    ClearesultCreditReportView, ClearesultSiteDefaultConfigViewset,
    ClearesultCoursesConfigViewset, SiteMandatoryCoursesView, UserParticipationGroups,
    ParticipationGroupCodeVerification
)


app_name = 'api_v0'

urlpatterns = (
    url(
        r'^user_credit_profiles/$',
        UserCreditProfileViewset.as_view({
            'get': 'list',
            'post': 'create'
        }),
        name="user_credit_profiles_list"
    ),
    url(
        r'^user_credit_profile/(?P<pk>\d+)/$',
        UserCreditProfileViewset.as_view({
            'patch': 'partial_update',
            'delete': 'destroy'
        }),
        name="user_credit_profile_detail"
    ),
    url(
        r'^credit_providers/$',
        ClearesultCreditProviderListView.as_view(),
        name="credit_providers_list"
    ),
    url(
        r'^courses/$',
        ClearesultCourseViewset.as_view({
            'get': 'list',
        }),
        name="clearesult_courses"
    ),
    url(
        r'^catalogs/$',
        ClearesultCatalogViewset.as_view({
            'get': 'list',
            'post': 'create'
        }),
        name="clearesult_catalogs"
    ),
    url(
        r'^catalogs/(?P<pk>\d+)',
        ClearesultCatalogViewset.as_view({
            'get': 'retrieve',
            'patch': 'partial_update',
            'delete': 'destroy'
        }),
        name="clearesult_catalog_detail"
    ),
    url(
        r'^sites/$',
        SiteViewset.as_view({
            'get': 'list'
        }),
        name="clearesult_sites"
    ),
    url(
        r'^site_linked_objects/(?P<type>[^/]+)/(?P<site_pk>\d+)/$',
        SiteLinkedObjectsListView.as_view(),
        name="site_linked_objects"
    ),
    url(
        r'^user_groups/$',
        ClearesultGroupViewset.as_view({
            'get': 'list',
            'post': 'create'
        }),
        name="clearesult_groups_list"
    ),
    url(
        r'^user_groups/(?P<pk>\d+)/$',
        ClearesultGroupViewset.as_view({
            'patch': 'partial_update',
            'delete': 'destroy'
        }),
        name="user_groups_detail"
    ),
    url(
        r'^mandatory_courses/$',
        ClearesultMandatoryCoursesViewset.as_view({
            'get': 'list',
        }),
        name="mandatory_courses_list"
    ),
    url(
        r'^mandatory_courses/(?P<pk>\d+)/$',
        ClearesultMandatoryCoursesViewset.as_view({
            'patch': 'partial_update',
            'get': 'retrieve'
        }),
        name="mandatory_courses_detail"
    ),
    url(
        r'^group_catalogs/$',
        ClearesultGroupCatalogsViewset.as_view({
            'get': 'list',
        }),
        name="clearesult_group_catalogs_list"
    ),
    url(
        r'^update_group_catalogs/$',
        ClearesultUpdateGroupCatalogsViewset.as_view({
            'post': 'update'
        }),
        name="clearesult_group_catalogs_update"
    ),
    url(
        r'^logout/$',
        ClearesultLogoutView.as_view(),
        name="clearesult_logout"
    ),
    url(
        r'^retake_course$',
        retake_course,
        name='clearesult_retake_course'
    ),
    url(
        r'^earned_credit_report/$',
        ClearesultCreditReportView.as_view(),
        name='earned_credit_report'
    ),
    url(
        r'^clearesult_site_config/$',
        ClearesultSiteDefaultConfigViewset.as_view({
            'get': 'list'
        }),
        name="clearesult_site_config_list"
    ),
    url(
        r'^clearesult_site_config/(?P<site_pk>\d+)/$',
        ClearesultSiteDefaultConfigViewset.as_view({
            'post': 'update'
        }),
        name="clearesult_site_configs_update"
    ),
    url(
        r'^clearesult_course_config/(?P<site_pk>\d+)/$',
        ClearesultCoursesConfigViewset.as_view({
            'get': 'list',
            'post': 'create'
        }),
        name="clearesult_course_config_list"
    ),
    url(
        r'^clearesult_course_config/(?P<site_pk>\d+)/(?P<pk>\d+)/$',
        ClearesultCoursesConfigViewset.as_view({
            'patch': 'partial_update',
            'delete': 'destroy',
            'get': 'retrieve'
        }),
        name="clearesult_course_config_details"
    ),
    url(
        r'^site_mandatory_courses/(?P<site_pk>\d+)/$',
        SiteMandatoryCoursesView.as_view(),
        name="site_mandatory_courses_list"
    ),
    url(
        r'^user_participation_groups/$',
        UserParticipationGroups.as_view(),
        name="user_participation_groups"
    ),
    url(
        r'^verify_participation_group_code/$',
        ParticipationGroupCodeVerification.as_view(),
        name="verify_participation_group_code"
    )
)
