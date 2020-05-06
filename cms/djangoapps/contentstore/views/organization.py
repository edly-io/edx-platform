"""Organizations views for use with Studio."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.generic import View
from django.contrib.sites.shortcuts import get_current_site

from openedx.features.edly.models import EdlySubOrganization

from openedx.core.djangolib.js_utils import dump_js_escaped_json


class OrganizationListView(View):
    """View rendering organization list as json.

    This view renders organization list json which is used in org
    autocomplete while creating new course.
    """

    @method_decorator(login_required)
    def get(self, request, *args, **kwargs):
        """Returns organization list as json."""
        current_site = get_current_site(request)
        edly_sub_organization = EdlySubOrganization.objects.filter(studio_site=current_site.id)
        org_names_list = [(org.edx_organization.short_name ) for org in edly_sub_organization]
        return HttpResponse(dump_js_escaped_json(org_names_list), content_type='application/json; charset=utf-8')
