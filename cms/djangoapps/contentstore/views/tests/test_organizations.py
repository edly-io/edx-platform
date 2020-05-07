"""Tests covering the Organizations listing on the Studio home."""
import json

from django.urls import reverse
from django.test import TestCase
from mock import patch
from waffle.testutils import override_switch

from student.tests.factories import UserFactory
from util.organizations_helpers import add_organization
from openedx.features.edly.tests.factories import EdlyOrganizationFactory, EdlySubOrganizationFactory, SiteFactory
from django.conf import settings

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
        self.assertEqual(response.status_code, 302)

    def test_organization_list(self):
        """
        Verify that the organization names list API only returns Edly's enabled organizations.
        """

        studio_site = SiteFactory()
        edly_organization = EdlyOrganizationFactory(name='Test Edly Organization Name')
        edly_sub_organization = EdlySubOrganizationFactory(
            name='Test Edly Sub Organization Name',
            slug='test-edly-sub-organization-name',
            studio_site=studio_site,
            edly_organization=edly_organization
        )

        edx_organization = edly_sub_organization.edx_organization
        edx_organization.short_name = 'test-edx-organization'
        edx_organization.save()

        response = self.client.get(self.org_names_listing_url, HTTP_ACCEPT='application/json', SERVER_NAME=studio_site.domain)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0], edly_sub_organization.edx_organization.short_name)

        """
        Now verify that if there is no "EdlySubOrganization" linked to a studio site the organization names list API returns empty response.
        """
        studio_site_2 = SiteFactory()
        response = self.client.get(self.org_names_listing_url, HTTP_ACCEPT='application/json', SERVER_NAME=studio_site_2.domain)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
