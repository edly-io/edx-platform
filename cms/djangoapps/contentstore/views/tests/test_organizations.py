"""Tests covering the Organizations listing on the Studio home."""
import json

from django.urls import reverse
from django.test import TestCase
from mock import patch

from student.tests.factories import UserFactory
from util.organizations_helpers import add_organization
from openedx.features.edly.tests.factories import EdlyOrganizationFactory, EdlySubOrganizationFactory, SiteFactory

@patch.dict('django.conf.settings.FEATURES', {'ORGANIZATIONS_APP': True})
class TestOrganizationListing(TestCase):
    """Verify Organization listing behavior."""
    @patch.dict('django.conf.settings.FEATURES', {'ORGANIZATIONS_APP': True})
    def setUp(self):
        super(TestOrganizationListing, self).setUp()
        self.staff = UserFactory(is_staff=True)
        self.client.login(username=self.staff.username, password='test')
        self.org_names_listing_url = reverse('organizations')

    def test_organization_list(self):
        """
        Verify that the organization names list api returns list of organization short names.
        """

        studio_site = SiteFactory()
        edly_organization = EdlyOrganizationFactory(name='Test Edly Organization Name')
        edly_sub_organization = EdlySubOrganizationFactory(
            name='Test Edly Sub Organization Name',
            slug='test-edly-sub-organization-name',
            studio_site=studio_site,
            edly_organization=edly_organization
        )

        organization = edly_sub_organization.edx_organization
        organization.short_name  = 'test-edx-organization'
        organization.save();

        response = self.client.get(self.org_names_listing_url, HTTP_ACCEPT='application/json', SERVER_NAME=studio_site.domain)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0], edly_sub_organization.edx_organization.short_name)
