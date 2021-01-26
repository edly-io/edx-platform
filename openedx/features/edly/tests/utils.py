from django.conf import settings
from django.test.client import RequestFactory

from rest_framework.test import APIClient

from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.features.edly import cookies
from openedx.features.edly.tests.factories import EdlySubOrganizationFactory

def get_edly_middleware_authorized_client(enforce_csrf_checks=False, domain=None, client_class=None):
    """
    """
    request = RequestFactory().get('/')
    if domain:
        request.site = SiteFactory(domain=domain)
    else:
        request.site = SiteFactory()

    EdlySubOrganizationFactory(lms_site=request.site)
    client_class = client_class if client_class else APIClient
    client = client_class(
        SERVER_NAME=request.site.domain,
        enforce_csrf_checks=enforce_csrf_checks
    )
    client.cookies.load(
        {
            settings.EDLY_USER_INFO_COOKIE_NAME: cookies._get_edly_user_info_cookie_string(request)
        }
    )
    return client
