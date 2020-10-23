"""
Tests for Edly API serializers.
"""
from django.test import TestCase, RequestFactory

from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory
from openedx.features.edly.api.serializers import UserSiteSerializer
from openedx.features.edly.tests.factories import EdlySubOrganizationFactory


class UserSiteSerializerTests(TestCase):

    def setUp(self):
        """
        Setup initial test data
        """
        super(UserSiteSerializerTests, self).setUp()
        self.request = RequestFactory().get('')
        self.edly_sub_org_of_user = EdlySubOrganizationFactory()
        self.context = {
            'request': self.request,
            'edly_sub_org_of_user': self.edly_sub_org_of_user,
        }
        self.serializer = UserSiteSerializer
        self.test_site_configuration = {
            'MOBILE_APP_CONFIG': {
                'COURSE_SHARING_ENABLED': True,
                'COURSE_VIDEOS_ENABLED': False,
                'COURSE_DATES_ENABLED': False,
            },
            'BRANDING': {
                'favicon': 'fake-favicon-url',
                'logo': 'fake-logo-url',
                'logo-white': 'fake-logo-white-url',
            },
            'COLORS': {
                'primary': 'fake-color',
                'secondary': 'fake-color',
            },
            'SITE_NAME': self.edly_sub_org_of_user.lms_site.domain,
            'course_org_filter': self.edly_sub_org_of_user.edx_organization.short_name,
        }
        SiteConfigurationFactory(
            site=self.edly_sub_org_of_user.lms_site,
            enabled=True,
            values=self.test_site_configuration,
        )

    def test_get_app_config(self):
        """
        Verify that `get_app_config` returns correct value.
        """
        serializer = self.serializer({}, context=self.context)

        for mobile_app_config_key, mobile_app_config_value in self.test_site_configuration['MOBILE_APP_CONFIG'].items():
            assert mobile_app_config_value == serializer.data['app_config'].get(mobile_app_config_key)

        assert self.edly_sub_org_of_user.edx_organization.short_name == serializer.data['app_config'].get('ORGANIZATION_CODE')

        protocol = 'https' if self.request.is_secure() else 'http'
        url = self.test_site_configuration['SITE_NAME']
        expected_api_host_url = '{}://{}'.format(protocol, url) if url else ''
        assert expected_api_host_url == serializer.data['app_config'].get('API_HOST_URL')

    def test_site_data(self):
        """
        Verify that `site_data` returns correct value.
        """
        serializer = self.serializer({}, context=self.context)

        for branding_key, branding_value in self.test_site_configuration['BRANDING'].items():
            assert branding_value == serializer.data['site_data'].get(branding_key)

        for color_key, color_value in self.test_site_configuration['COLORS'].items():
            assert color_value == serializer.data['site_data'].get(color_key)

        assert self.edly_sub_org_of_user.lms_site.name == serializer.data['site_data'].get('display_name')
