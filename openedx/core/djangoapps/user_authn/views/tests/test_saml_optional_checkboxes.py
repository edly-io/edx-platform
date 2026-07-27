"""
Tests for SAML provider configuration to skip optional checkboxes in registration form.
"""

import logging
from unittest import mock

from django.test import TestCase, override_settings
from django.test.client import RequestFactory

from common.djangoapps.third_party_auth.tests.testutil import simulate_running_pipeline
from openedx.core.djangoapps.user_authn.views.registration_form import RegistrationFormFactory

log = logging.getLogger(__name__)


class SAMLProviderOptionalCheckboxTest(TestCase):
    """
    Tests for SAML provider configuration options to skip optional checkboxes
    (marketing emails, etc.) during registration.
    """

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.factory = RequestFactory()

    def _create_request(self):
        """Create a test request with session support."""
        from importlib import import_module
        from django.conf import settings as django_settings

        request = self.factory.get('/register')
        engine = import_module(django_settings.SESSION_ENGINE)
        request.session = engine.SessionStore(None)
        return request
