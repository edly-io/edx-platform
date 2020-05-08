"""Tests covering the Organizations listing on the Studio home."""
import json
import logging

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from mock import patch
from testfixtures import LogCapture
from waffle.testutils import override_switch
from openedx.features.edly.tests.factories import (EdlyOrganizationFactory,
                                                   EdlySubOrganizationFactory,
                                                   SiteFactory)
from student.tests.factories import UserFactory
from util.organizations_helpers import add_organization

LOGGER_NAME = 'openedx.features.edly.utils'


@patch.dict('django.conf.settings.FEATURES', {'ORGANIZATIONS_APP': True})
@override_switch(settings.ENABLE_EDLY_ORGANIZATIONS_SWITCH, active=False)
class TestOrganizationListing(TestCase):
    """Verify Organization listing behavior."""
    @patch.dict('django.conf.settings.FEATURES', {'ORGANIZATIONS_APP': True})
    def setUp(self):
        super(TestOrganizationListing, self).setUp()
        self.staff = UserFactory(is_staff=True)
        self.client.login(username=self.staff.username, password='test')
        self.org_names_listing_url = reverse('organizations')
        self.org_short_names = ["alphaX", "betaX", "orgX"]
        for index, short_name in enumerate(self.org_short_names):
            add_organization(organization_data={
                'name': 'Test Organization %s' % index,
                'short_name': short_name,
                'description': 'Testing Organization %s Description' % index,
            })

    def test_organization_list(self):
        """Verify that the organization names list api returns list of organization short names."""
        response = self.client.get(self.org_names_listing_url, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        org_names = json.loads(response.content)
        self.assertEqual(org_names, self.org_short_names)


@patch.dict('django.conf.settings.FEATURES', {'ORGANIZATIONS_APP': True})
@override_switch(settings.ENABLE_EDLY_ORGANIZATIONS_SWITCH, active=True)
class TestEdlyOrganizationListing(TestCase):
    """
    Verify Organization listing behavior.
    """
    @patch.dict('django.conf.settings.FEATURES', {'ORGANIZATIONS_APP': True})
    def setUp(self):
        super(TestEdlyOrganizationListing, self).setUp()
        self.staff = UserFactory(is_staff=True)
        self.client.login(username=self.staff.username, password='test')
        self.org_names_listing_url = reverse('organizations')

    def test_without_authentication(self):
        """
        Verify authentication is required when accessing the endpoint.
        """
        self.client.logout()
        response = self.client.get(self.org_names_listing_url)
        assert response.status_code == 302

    def test_organization_list(self):
        """
        Verify that the organization names list API only returns Edly's enabled organizations.
        """

        studio_site = SiteFactory()
        edly_organization = EdlyOrganizationFactory(name='Test Edly Organization Name')
        edly_sub_organization = EdlySubOrganizationFactory(
            studio_site=studio_site,
            edly_organization=edly_organization
        )

        response = self.client.get(self.org_names_listing_url, HTTP_ACCEPT='application/json', SERVER_NAME=studio_site.domain)

        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0] == edly_sub_organization.edx_organization.short_name

    def test_organization_list_without_linked_edly_sub_organization(self):
        """
        Verify that if there is no "EdlySubOrganization" linked to a studio site the organization names list API returns empty response.
        """
        studio_site = SiteFactory()
        with LogCapture(LOGGER_NAME) as logger:
            response = self.client.get(self.org_names_listing_url, HTTP_ACCEPT='application/json', SERVER_NAME=studio_site.domain)

            logger.check(
                (
                    LOGGER_NAME,
                    'ERROR',
                    'No EdlySubOrganization found for site {}'.format(studio_site)
                )
            )

            assert response.status_code == 200
            assert response.json() == []
