"""
Django views for colaraz features application
"""
import json
from requests.models import PreparedRequest

from django.conf import settings
from django.urls import reverse, reverse_lazy
from django.views.generic.base import RedirectView
from django.views.generic import ListView
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from student.models import CourseAccessRole
from student.helpers import get_next_url_for_login_page
from edxmako.shortcuts import Engines

import third_party_auth
from openedx.features.colaraz_features.helpers import get_site_base_url
from openedx.features.colaraz_features.forms import ColarazCourseAccessRoleForm
from openedx.features.colaraz_features.filters import CourseAccessRoleFilterMixin
from openedx.features.colaraz_features.permissions import IsAdminOrOrganizationalRoleManager


class AuthProviderLogoutRedirectView(RedirectView):

    def get_redirect_url(self, *args, **kwargs):
        """
        Redirects user to relative social auth provider for logout process.
        In-case no auth provider is found or the logout_url is missing in provider's configurations
        the user is redirected to edX's default logout page '/logout'
        """
        backend_name = getattr(settings, 'COLARAZ_AUTH_PROVIDER_BACKEND_NAME', None)
        if third_party_auth.is_enabled() and backend_name and self.request.session.has_key('id_token'):
            provider = [enabled for enabled in third_party_auth.provider.Registry.enabled()
                        if enabled.backend_name == backend_name]
            if provider:
                logout_url = json.loads(getattr(provider[0], 'other_settings', '{}')).get('logout_url')
                if logout_url:
                    redirect_to = self.request.META.get('HTTP_REFERER') or get_site_base_url(self.request)
                    params = {
                        'id_token_hint': self.request.session['id_token'],
                        'post_logout_redirect_uri': redirect_to
                    }
                    req = PreparedRequest()
                    req.prepare_url(logout_url, params)

                    return req.url

        return reverse('logout')


class BaseViewMixin(object):
    """
    Mixin for setting template engine and enforcing authentication before page view.
    """
    template_engine = Engines.MAKO
    permission_classes = (IsAdminOrOrganizationalRoleManager, )

    @classmethod
    def as_view(cls, **initkwargs):
        """
        Override `as_view` to perform login check.
        """
        return login_required(
            super(BaseViewMixin, cls).as_view(**initkwargs)
        )

    def dispatch(self, request, *args, **kwargs):
        """
        Override dispatch to perform permission checks.
        """
        if not self.check_permission():
            return redirect('/login?next=' + get_next_url_for_login_page(request))

        return super(BaseViewMixin, self).dispatch(request, *args, **kwargs)

    def check_permission(self):
        """
        Validate that current request is authorized or not.

        Returns:
            (bool): True if current request is authorized, False otherwise.
        """

        return all(permission().has_permission(self.request, self) for permission in self.permission_classes)


class ColarazFormViewMixin(object):
    """
    Mixin to add functionality required by the Colaraz Form.
    """
    def get_form_kwargs(self):
        """
        Returns the keyword arguments for instantiating the form.
        """
        kwargs = super(ColarazFormViewMixin, self).get_form_kwargs()

        kwargs.update({
            'user': self.request.user
        })
        return kwargs


class CourseAccessRoleListView(BaseViewMixin, CourseAccessRoleFilterMixin, ListView):
    """
    Handle requests to course access role management.
    """
    template_name = 'course-access-roles-list.html'
    model = CourseAccessRole
    paginate_by = 10

    def get_paginate_by(self, queryset):
        """
        Get the number of items to paginate by, or ``None`` for no pagination.
        """
        return self.paginate_by if queryset.count() > self.paginate_by else None


class CourseAccessRoleCreateView(BaseViewMixin, ColarazFormViewMixin, CreateView):
    """
    Handle requests to course access role management.
    """
    template_name = 'course-access-roles-create.html'
    form_class = ColarazCourseAccessRoleForm

    success_url = reverse_lazy('colaraz_features:course-access-roles-list')


class CourseAccessRoleUpdateView(BaseViewMixin, CourseAccessRoleFilterMixin, ColarazFormViewMixin, UpdateView):
    """
    Handle requests to course access role management.
    """
    template_name = 'course-access-roles-update.html'
    form_class = ColarazCourseAccessRoleForm
    queryset = CourseAccessRole.objects.all()

    success_url = reverse_lazy('colaraz_features:course-access-roles-list')


class CourseAccessRoleDeleteView(BaseViewMixin, CourseAccessRoleFilterMixin, DeleteView):
    """
    Handle requests to course access role management.
    """
    template_name = 'course-access-roles-delete.html'
    queryset = CourseAccessRole.objects.all()

    success_url = reverse_lazy('colaraz_features:course-access-roles-list')
