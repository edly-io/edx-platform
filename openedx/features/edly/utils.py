import waffle
from django.conf import settings
from django.contrib.sites.shortcuts import get_current_site
from openedx.features.edly.models import EdlySubOrganization
from util.organizations_helpers import get_organizations

def get_enabled_organization(request):
  """
  Helper method to get linked organizations for request site.

  Returns:
      list: List of linked organizations for request site
  """

  if not waffle.switch_is_active(settings.ENABLE_EDLY_ORGANIZATIONS_SWITCH):
      organizations = get_organizations()
  else:
      current_site = get_current_site(request)
      edly_organizations = EdlySubOrganization.objects.filter(studio_site=current_site.id)
      organizations = [(org.edx_organization.__dict__) for org in edly_organizations]

  return organizations
