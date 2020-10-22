"""
Tests for Edly API ViewSets.
"""
from django.test import TestCase, RequestFactory, Client
from django.urls import reverse

from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.features.edly.tests.factories import EdlyUserFactory, EdlySubOrganizationFactory


class TestUserSitesViewSet(TestCase):

    def setUp(self):
        """
        Setup initial test data
        """
        super(TestUserSitesViewSet, self).setUp()
        self.request = RequestFactory().get('/')
        self.request.site = SiteFactory()
        self.edly_sub_org = EdlySubOrganizationFactory()
        self.user = EdlyUserFactory()
        self.client = Client(SERVER_NAME=self.request.site.domain)
        self.client.login(username=self.user.username, password='test')

    def test_list(self):
        """
        Verify that `list` returns correct response.
        """
        user_sites_list_url = reverse('user-sites-list')
        response = self.client.get(user_sites_list_url)
        import pdb;pdb.set_trace()
        assert response
