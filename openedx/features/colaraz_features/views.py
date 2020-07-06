"""
Django views for colaraz features application
"""
import json

from requests.models import PreparedRequest

import third_party_auth
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import HttpResponseBadRequest, JsonResponse, Http404
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import ListView
from django.views.generic.base import RedirectView
from django.views.generic.edit import CreateView, DeleteView, UpdateView
from edxmako.shortcuts import Engines
from openedx.features.colaraz_features.constants import ALL_ORGANIZATIONS_MARKER
from openedx.features.colaraz_features.exceptions import HttpBadRequest
from openedx.features.colaraz_features.filters import CourseAccessRoleFilterMixin
from openedx.features.colaraz_features.forms import ColarazCourseAccessRoleForm
from openedx.features.colaraz_features.helpers import (
    get_course_access_role_display_name,
    get_organization_users,
    get_site_base_url,
    get_user_organizations,
    notify_access_role_deleted
)
from openedx.features.colaraz_features.permissions import IsAdminOrOrganizationalRoleManager
from student.helpers import get_next_url_for_login_page
from student.models import CourseAccessRole
from student.roles import CourseCreatorRole

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



class CourseAccessRolesPaginator(Paginator):

    def __init__(self, object_list, per_page, orphans=0, allow_empty_first_page=True):
        records = {}
        course_creator_roles = {}
        org_roles = []
        for obj in object_list:
            if obj.role == CourseCreatorRole.ROLE:
                course_creator_roles.update({obj.user.email: obj})
                continue
            email_id = obj.user.email
            course_id = str(obj.course_id) if obj.course_id else '-'
            key = '{},{},{}'.format(email_id, course_id, obj.org)
            record = records.get(key, {})
            role = get_course_access_role_display_name(obj)
            record['email'] = email_id
            record['username'] = obj.user.profile.name or obj.user.username
            record['orgs'] = self._append_list(record.get('orgs', []), obj.org)
            record['roles'] = self._append_list(record.get('roles', []), role)
            record['ids'] = self._append_list(record.get('ids', []), str(obj.id))
            if not obj.course_id:
                org_roles.append(record)

            records.update({key: record})
        
        used_roles_email_ids = []
        for org_role in org_roles:
            if course_creator_roles.has_key(org_role.get('email')):
                creator_role = course_creator_roles.get(org_role.get('email'))
                used_roles_email_ids.append(org_role.get('email'))
                role_display_name = get_course_access_role_display_name(creator_role)
                org_role['roles'] = self._append_list(org_role.get('roles', []), role_display_name)
                org_role['ids'] = self._append_list(org_role.get('ids', []), str(creator_role.id))
        
        for email, role in course_creator_roles.items():
            if email not in used_roles_email_ids:
                records.update({
                    '{},-,-'.format(email): {
                        'username': role.user.profile.name or role.user.username,
                        'ids': [str(role.id)],
                        'roles': [role.role],
                        'orgs': []
                    }
                })

        obj_list = []
        for key in sorted(records.keys()):
            obj_list.append({key: records[key]})

        super(CourseAccessRolesPaginator, self).__init__(
            obj_list, per_page, orphans,
            allow_empty_first_page
        )

    @staticmethod
    def _append_list(record, role):
        if role and role not in record:
            record.append(role)
        return record


class CourseAccessRoleListView(BaseViewMixin, CourseAccessRoleFilterMixin, ListView):
    """
    Handle requests to course access role management.
    """
    template_name = 'course-access-roles-list.html'
    model = CourseAccessRole
    paginate_by = 20
    paginator_class = CourseAccessRolesPaginator
    ordering = ['user', 'course_id']

    def get_paginate_by(self, queryset):
        """
        Get the number of items to paginate by, or ``None`` for no pagination.
        """
        return self.paginate_by
        
    def get_queryset(self):
        """
        Return the list of items for this view. Apply search on queryset.
        """
        queryset = super(CourseAccessRoleListView, self).get_queryset()
        q = self.request.GET.get('q', '')
        if q:
            queryset = queryset.filter(user__email__icontains=q)
        return queryset


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

    def get_object(self):
        queryset = super(CourseAccessRoleUpdateView, self).get_queryset()

        pk = self.kwargs.get(self.pk_url_kwarg, '')
        try:
            ids = [int(key) for key in pk.split(',') if key.strip()]
        except (TypeError, ValueError):
            raise Http404('User Role Not Found')
        
        if not ids:
            raise Http404('User Role Not Found')

        queryset = queryset.filter(id__in=ids)
        if len(queryset) > 0:
            return queryset
        
        raise Http404('User Role Not Found')

class CourseAccessRoleDeleteView(BaseViewMixin, CourseAccessRoleFilterMixin, DeleteView):
    """
    Handle requests to course access role management.
    """
    object = None
    template_name = 'course-access-roles-delete.html'
    queryset = CourseAccessRole.objects.all()

    success_url = reverse_lazy('colaraz_features:course-access-roles-list')

    def get(self, request, *args, **kwargs):
        try:
            objects = self.get_objects()
        except HttpBadRequest as error:
            return HttpResponseBadRequest(error.message)

        self.object = objects[0]

        context = self.get_context_data(objects=objects)
        return self.render_to_response(context)

    def delete(self, request, *args, **kwargs):
        """
        Calls the delete() method on the fetched object and then
        redirects to the success URL.
        """
        try:
            objects = self.get_objects()
        except HttpBadRequest as error:
            return HttpResponseBadRequest(error.message)

        self.object = objects[0]
        success_url = self.get_success_url()

        # Delete the records
        for obj in objects:
            obj.delete()
            notify_access_role_deleted(obj, request.user)

        return redirect(success_url)

    def get_pks_from_url(self):
        """
        Get the list of primary keys from the url.
        """
        pk = self.kwargs.get(self.pk_url_kwarg)

        try:
            ids = [int(key) for key in pk.split(',') if key.strip()]
        except (TypeError, ValueError):
            raise HttpBadRequest('Invalid Request URL')
        else:
            if not ids:
                raise HttpBadRequest('Invalid Request URL')

        return ids

    def get_objects(self):
        """
        Returns the objects the view is displaying.
        """
        queryset = self.get_queryset()
        ids = self.get_pks_from_url()

        queryset = queryset.filter(id__in=ids)
        if queryset.count() != len(ids):
            # User is trying something fishy
            raise Http404('User Role Not Found')

        return queryset.all()


class UserListView(BaseViewMixin, View):
    def get(self, request):
        """
        JSON list of users to be used by the select2 field in course access role form.

        Response json should have the following format to be compatible with select2.
        {
            "results": [
                {
                "id": 1,
                "text": "edx@example.com"
                },
                {
                "id": 2,
                "text": "staff@example.com"
                }
            ],
            "pagination": {
                "more": true
            }
        }
        """
        page = self.request.GET.get('page') or 1
        search = self.request.GET.get('search') or ''

        try:
            page = int(page)
        except (ValueError, TypeError):
            page = 1

        if self.request.user.is_staff:
            user_organizations = {ALL_ORGANIZATIONS_MARKER}
        else:
            user_organizations = get_user_organizations(self.request.user)

        users, has_more = get_organization_users(user_organizations, search, page=page)
        return JsonResponse({
            'results': [{'id': user.id, 'text': user.email} for user in users],
            'pagination': {'more': has_more}
        })
