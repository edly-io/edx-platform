"""Organizations views for use with Studio."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.generic import View

from openedx.core.djangolib.js_utils import dump_js_escaped_json
from util.organizations_helpers import get_organizations, filter_authorized_organizations_from_list


class OrganizationListView(View):
    """View rendering organization list as json.

    This view renders organization list json which is used in org
    autocomplete while creating new course.
    """

    @method_decorator(login_required)
    def get(self, request, *args, **kwargs):
        """Returns organization list as json."""
        organizations = get_organizations()
        # [COLARAZ_CUSTOM]
        # Restrict user from creating courses with non-authorized organizations
        org_names_list = filter_authorized_organizations_from_list(
            organizations, request.user.is_superuser)
        return HttpResponse(
            dump_js_escaped_json(org_names_list), content_type='application/json; charset=utf-8')
