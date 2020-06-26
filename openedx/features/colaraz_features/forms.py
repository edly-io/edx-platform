"""
Serializers for colaraz application.
"""
from django import forms
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils.safestring import mark_safe
from opaque_keys.edx.django.models import CourseKeyField
from openedx.features.colaraz_features.constants import (
    ALL_ORGANIZATIONS_MARKER,
    ALL_ROLES,
    COURSE_ROLES,
    EMPTY_OPTION,
    GLOBAL_ROLES,
    ORG_ROLES
)
from openedx.features.colaraz_features.fields import MultipleChoiceCourseIdField
from openedx.features.colaraz_features.helpers import (
    bulk_create_course_access_role,
    get_user_organizations,
    revoke_course_creator_access
)
from student.models import CourseAccessRole
from student.roles import CourseCreatorRole


class ColarazCourseAccessRoleForm(forms.Form):
    user = forms.ChoiceField(
        required=True,
        label='User',
        label_suffix=' *',
    )
    org = forms.SlugField(
        required=True,
        label='Organization',
        label_suffix=' *',
    )
    course_ids = MultipleChoiceCourseIdField(
        is_dynamic=True,
        required=False,
        label='Course Identifiers',
    )

    roles = forms.MultipleChoiceField(
        choices=ALL_ROLES,
        label='Roles',
        label_suffix=' *',
        widget=forms.CheckboxSelectMultiple(),
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        self.instances = kwargs.pop('instance')
        self.instance = self.instances.first() if self.instances else None

        if self.instance:
            initial = kwargs.get('kwargs', None)
            model_data = {
                'user': self.instance.user.id,
                'org': self._get_selected_user_org(),
                'course_ids': self._get_selected_course_ids(),
                'roles': self._get_selected_roles(),
            }
            if initial:
                model_data.update(initial)
            kwargs['initial'] = model_data

        if self.user.is_staff:
            self.user_organizations = {ALL_ORGANIZATIONS_MARKER}
        else:
            self.user_organizations = get_user_organizations(self.user)

        # Pre Populate input fields
        self._pre_populate_roles_choices()
        self._pre_populate_org()
        self._pre_populate_course_id_choices(kwargs)
        self._pre_populate_user_options(kwargs)

        super(ColarazCourseAccessRoleForm, self).__init__(*args, **kwargs)

    def _get_selected_user_org(self):
        return self.instance.org or self.instance.user.colaraz_profile.site_identifier

    def _get_selected_roles(self):
        selected_roles = []
        for instance in self.instances:
            if not instance.course_id and instance.role != CourseCreatorRole.ROLE:
                selected_roles.append('org_{}'.format(instance.role))
            else:
                selected_roles.append(instance.role)
        return selected_roles

    def _get_selected_course_ids(self):
        selected_course_ids = []
        for instance in self.instances:
            if instance.course_id and str(instance.course_id) not in selected_course_ids:
                selected_course_ids.append(str(instance.course_id))
        return selected_course_ids

    def _pre_populate_user_options(self, init_kwargs):
        """
        Populate options for user select input.
        """
        # Incoming data should always get priority, do not change the order of if-else
        if 'data' in init_kwargs and 'user' in init_kwargs['data']:
            try:
                user = User.objects.filter(id=init_kwargs['data']['user']).get()
            except User.DoesNotExist:
                user_options = []
            else:
                user_options = [(user.id, user.email)]
        elif self.instance:
            user_options = [(self.instance.user.id, self.instance.user.email)]
        else:
            user_options = []

        # add an empty option to avoid auto selection in the browser.
        self.declared_fields['user'].choices = [EMPTY_OPTION] + user_options
        self.declared_fields['user'].disabled = True if self.instance else False

    def _pre_populate_roles_choices(self):
        """
        Populate roles input field.
        """
        choices = ALL_ROLES
        if self.instance:
            choices = COURSE_ROLES if self.instance.course_id else ORG_ROLES + GLOBAL_ROLES

        self.declared_fields['roles'].choices = choices

    def _pre_populate_org(self):
        """
        Populate org input field.
        """
        if ALL_ORGANIZATIONS_MARKER not in self.user_organizations and len(self.user_organizations) == 1:
            # Disable Org field if user can use only a single organization.
            self.declared_fields['org'].initial = self.user_organizations.copy().pop()
            self.declared_fields['org'].disabled = True
        elif not self.instance:
            self.declared_fields['org'].initial = ''
            self.declared_fields['org'].disabled = False
        else:
            self.declared_fields['org'].disabled = True

    def _pre_populate_course_id_choices(self, init_kwargs):
        """
        Populate course id input field.
        """
        initial = init_kwargs.get('initial', {})
        # Incoming data should always get priority, do not change the order of if-else
        if 'data' in init_kwargs and init_kwargs['data'].get('course_ids'):
            course_ids = init_kwargs['data'].getlist('course_ids')
            self.declared_fields['course_ids'].choices = [(course_id, course_id) for course_id in course_ids]
        elif initial and initial.get('course_ids'):
            self.declared_fields['course_ids'].choices = [(course_id, course_id) for course_id in initial['course_ids']]

        self.declared_fields['course_ids'].disabled = True if self.instance else False

    def clean_course_ids(self):
        """
        Clean and validate course id values.
        """
        course_ids = self.cleaned_data.get('course_ids')
        if len(course_ids) != len(set(course_ids)):
            raise forms.ValidationError('Duplicate Course Ids; Please remove the duplicate course ids.')
        org_names = {c.org for c in course_ids}

        if len(org_names) > 1:
            raise forms.ValidationError(
                u'All the courses must be of the same organization, given course organizations are {}.'.format(
                    ", ".join(org_names)
                )
            )

        return course_ids

    def clean_org(self):
        """
        Clean and validate organization in the payload.
        """
        org = self.cleaned_data['org']

        if not self.user.is_staff:
            if org not in self.user_organizations:
                raise forms.ValidationError(
                    'User can only manage roles of it\'s own organization. Try {}'.format(' or '.join(
                        self.user_organizations
                    ))
                )
        return org

    def clean(self):
        """
        Checking if any of the course access role already exists.
        """
        cleaned_data = super(ColarazCourseAccessRoleForm, self).clean()

        org_names = {c.org.lower() for c in cleaned_data.get('course_ids', [])}
        org = cleaned_data['org']

        if org_names and org.lower() not in org_names:
            raise forms.ValidationError(
                u'Organization name "{}" does not match with course organization "{}".'.format(
                    org, 'or '.join(org_names)
                )
            )

        if not self.errors and not self.instance:
            selected_roles = cleaned_data.get('roles')
            org_roles = [role.replace('org_', '', 1) for role in selected_roles
                         if role in [index[0] for index in ORG_ROLES + GLOBAL_ROLES]]

            duplicate_roles = CourseAccessRole.objects.filter(
                Q(
                    user_id=cleaned_data.get('user'),
                    org=cleaned_data.get('org'),
                    course_id__in=cleaned_data.get('course_ids'),
                    role__in=selected_roles,
                ) | Q(
                    user_id=cleaned_data.get('user'),
                    course_id=CourseKeyField.Empty,
                    role__in=org_roles,
                ) & Q(
                    Q(org=cleaned_data.get('org')) | Q(org='')
                )
            )

            if len(duplicate_roles) > 0:
                raise forms.ValidationError(
                    mark_safe(
                        'Given user already has the following access for the given organization.'
                        '<ul>{}</ul>'.format(
                            ''.join(
                                [
                                    '<li><strong>Course Id:</strong> {}, <strong>Role:</strong> {}</li>'.format(
                                        role.course_id, role.role
                                    ) for role in duplicate_roles
                                ]
                            )
                        )
                    )
                )

        return cleaned_data

    def save(self, commit=True):
        """
        Save course access roles, for create/update requests.
        """
        instance = self.instance if self.instance else CourseAccessRole()

        if self.errors:
            raise ValueError(
                "The %s could not be %s because the data didn't validate." % (
                    instance._meta.object_name,
                    'created' if instance._state.adding else 'changed',
                )
            )

        with transaction.atomic():
            if self.instance is None:
                # We are on the create page.
                return self.create()
            else:
                # We are on the update page
                return self.update()

    def create(self):
        """
        Create a bunch of course access roles, and return a single instance from them.
        """
        created_roles = bulk_create_course_access_role(
            self.user,
            user_id=self.cleaned_data['user'],
            org=self.cleaned_data['org'],
            roles=self.cleaned_data.get('roles', []),
            course_ids=self.cleaned_data.get('course_ids', [])
        )
        return created_roles[0] if len(created_roles) > 0 else None

    def update(self):
        """
        Update course access roles, deleting the ones removed by the user and return one instance.
        """
        def _is_course_creator_role_changed(initial_state, final_state):
            return CourseCreatorRole.ROLE in initial_state.get('roles', []) \
                and CourseCreatorRole.ROLE not in final_state.get('roles', [])

        if _is_course_creator_role_changed(self.initial, self.cleaned_data):
            revoke_course_creator_access(self.instance.user, self.user)
            self.instances.filter(role=CourseCreatorRole.ROLE).delete()

        cleaned_roles = []
        for role in self.cleaned_data.get('roles', []):
            cleaned_roles.append(role.replace('org_', '', 1) if role.startswith('org_') else role)

        self.instances.exclude(
            role__in=cleaned_roles,
        ).delete()

        created_roles = bulk_create_course_access_role(
            self.user,
            user_id=self.cleaned_data['user'],
            org=self.cleaned_data['org'],
            roles=self.cleaned_data.get('roles', []),
            course_ids=self.cleaned_data.get('course_ids', [])
        )
        return created_roles[0] if len(created_roles) > 0 else None
