from django.contrib.sites.shortcuts import get_current_site
from openedx.features.edly.models import EdlySubOrganization


def get_edly_organizations(request):
  """
  Returns Organization base on request site.
  """
  current_site = get_current_site(request)
  edly_organizations = EdlySubOrganization.objects.filter(studio_site=current_site.id)
  return edly_organizations
