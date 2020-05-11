"""
Serializers for colaraz application.
"""
from django import forms
from django.db import transaction
from django.utils.safestring import mark_safe
from django.db.models import Count
from django.contrib.auth.models import User

from student.models import CourseAccessRole
from student.roles import REGISTERED_ACCESS_ROLES

from opaque_keys.edx.django.models import CourseKeyField

from openedx.features.colaraz_features.helpers import (
    get_user_organizations, get_or_create_course_access_role, bulk_create_course_access_role
)
from openedx.features.colaraz_features.constants import (
    ALL_ORGANIZATIONS_MARKER, EMPTY_OPTION, LMS_ADMIN_ROLE, ROLES_FOR_LMS_ADMIN
)
from openedx.features.colaraz_features.fields import MultipleChoiceCourseIdField


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
        choices=[(role_name, role_name) for role_name in REGISTERED_ACCESS_ROLES.keys()],
        label='Roles',
        label_suffix=' *',
        widget=forms.CheckboxSelectMultiple(),
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        self.instance = kwargs.pop('instance')
        self.instances = None
        self.populate_instances()

        if self.instance is not None and self.instances is not None:
            initial = kwargs.get('kwargs', None)
            model_data = {
                'user': self.instance.user.id,
                'org': self.instance.org,
                'course_ids': list({str(role.course_id) for role in self.instances if role.course_id}),
                'roles': [role.role for role in self.instances],
            }
            if initial:
                model_data.update(initial)
            kwargs['initial'] = model_data

        if self.user.is_staff:
            self.user_organizations = {ALL_ORGANIZATIONS_MARKER}
        else:
            self.user_organizations = get_user_organizations(self.user)

        # Pre Populate input fields
        self.__pre_populate_roles()
        self.__pre_populate_org()
        self.__pre_populate_course_id(kwargs)
        self.__pre_populate_user_options(kwargs)

        super(ColarazCourseAccessRoleForm, self).__init__(*args, **kwargs)

    def __pre_populate_user_options(self, init_kwargs):
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

    def __pre_populate_roles(self):
        """
        Populate roles input field.
        """
        # We need to remove the LMS Admin choice explicitly, otherwise it will appear on update page and
        # duplicated options on create page.
        self.declared_fields['roles'].choices = [
            choice for choice in self.declared_fields['roles'].choices if LMS_ADMIN_ROLE not in choice
        ]
        if self.instance is None:
            # Add LMS Admin role to the option for create page.
            self.declared_fields['roles'].choices += [(LMS_ADMIN_ROLE, LMS_ADMIN_ROLE)]

    def __pre_populate_org(self):
        """
        Populate org input field.
        """
        if ALL_ORGANIZATIONS_MARKER not in self.user_organizations and len(self.user_organizations) == 1:
            # Disable Org field if user can use only a single organization.
            self.declared_fields['org'].initial = self.user_organizations.copy().pop()
            self.declared_fields['org'].disabled = True

    def __pre_populate_course_id(self, init_kwargs):
        """
        Populate course id input field.
        """
        initial = init_kwargs.get('initial', {})
        # Incoming data should always get priority, do not change the order of if-else
        if 'data' in init_kwargs and 'course_ids' in init_kwargs['data']:
            course_ids = init_kwargs['data'].getlist('course_ids')
            self.declared_fields['course_ids'].choices = [(course_id, course_id) for course_id in course_ids]
        elif initial and 'course_ids' in initial:
            self.declared_fields['course_ids'].choices = [(course_id, course_id) for course_id in initial['course_ids']]

    def populate_instances(self):
        """
        Populate `instances` attribute for the form. It should contain list of all the instances being updated.
        """
        if self.instance is not None:
            # We are on the update page.
            if not self.instance.course_id:
                # If course id is not present then we are updating organization level access roles.
                # So pick all of the organization level courses belonging to the given user.
                self.instances = CourseAccessRole.objects.filter(
                    user=self.instance.user,
                    org=self.instance.org,
                    course_id=CourseKeyField.Empty
                )
            else:
                # Otherwise we need to pick all the course level access roles
                # We need to provide the ability to have course access roles that provide
                # different permissions to different courses for the same user and organization.

                # First, get all the roles assigned to the current user for the course on self.instance
                roles = set(CourseAccessRole.objects.filter(
                    user=self.instance.user,
                    org=self.instance.org,
                    course_id=self.instance.course_id,
                ).values_list('role', flat=True))

                access_roles = CourseAccessRole.objects.filter(
                    user=self.instance.user,
                    org=self.instance.org,
                ).exclude(
                    course_id=CourseKeyField.Empty
                ).values(
                    'course_id'
                ).annotate(
                    role_count=Count('role')
                )

                courses_to_include = [self.instance.course_id]
                instance_role_count = len(roles)  # This is used just for caching length of roles.
                for item in access_roles:
                    if item['role_count'] == instance_role_count:
                        # In order for the course access roles to be added to update page,
                        # they need to be exactly same for all courses. otherwise, bugs will appear.
                        # Check that roles are exactly the same along with the role count.
                        # we have to make a db query for this.
                        course_roles = set(CourseAccessRole.objects.filter(
                            user=self.instance.user,
                            org=self.instance.org,
                            course_id=item['course_id'],
                        ).values_list('role', flat=True))
                        if course_roles == roles:
                            # if instance roles and the course roles are exactly
                            # the same then we can add them to update on the same page
                            courses_to_include.append(item['course_id'])

                self.instances = CourseAccessRole.objects.filter(
                    user=self.instance.user,
                    org=self.instance.org,
                    course_id__in=courses_to_include,
                ).exclude(
                    course_id=CourseKeyField.Empty
                )

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

        roles = cleaned_data.get('roles', [])
        if LMS_ADMIN_ROLE in roles and cleaned_data.get('course_ids'):
            raise forms.ValidationError(
                '{} role can only be assigned on the organization level, '
                'please remove course selection to proceed.'.format(LMS_ADMIN_ROLE)
            )
        elif LMS_ADMIN_ROLE in roles:
            roles = self.cleaned_data.get('roles', [])
            roles.extend(ROLES_FOR_LMS_ADMIN)
            roles = set(roles)  # Remove duplicates
            roles.remove(LMS_ADMIN_ROLE)  # Remove LMS Admin Role

            if CourseAccessRole.objects.filter(
                user_id=cleaned_data.get('user'),
                org=cleaned_data.get('org'),
                role__in=ROLES_FOR_LMS_ADMIN
            ).exists():
                raise forms.ValidationError(
                    '{} role can not be assigned to the selected user as some or all of roles of the lms admin'
                    ' are already assigned to the selected user.'.format(LMS_ADMIN_ROLE, )
                )
            cleaned_data['roles'] = list(roles)

        org_names = {c.org.lower() for c in cleaned_data.get('course_ids', [])}
        org = cleaned_data['org']

        if org_names and org.lower() not in org_names:
            raise forms.ValidationError(
                u'Organization name "{}" does not match with course organization "{}".'.format(
                    org, 'or '.join(org_names)
                )
            )

        if not self.errors and not self.instance:
            duplicate_roles = CourseAccessRole.objects.filter(
                    user_id=cleaned_data.get('user'),
                    org=cleaned_data.get('org'),
                    course_id__in=cleaned_data.get('course_ids'),
                    role__in=cleaned_data.get('roles')
            ).all()
            if len(duplicate_roles) > 0:
                raise forms.ValidationError(
                    mark_safe(
                        'Given user already has the following access for the given organization.'
                        '<ul><li>{}</li></ul>'.format(
                            '</li>'.join(
                                [
                                    '<strong>Course Id:</strong> {}, <strong>Role:</strong> {}'.format(
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
        roles = bulk_create_course_access_role(
            self.user,
            user_id=self.cleaned_data['user'],
            org=self.cleaned_data['org'],
            roles=self.cleaned_data.get('roles', []),
            course_ids=self.cleaned_data.get('course_ids', [])
        )

        return roles[0] if len(roles) > 0 else None

    def update(self):
        """
        Update course access roles, deleting the ones removed by the user and return one instance.
        """
        # if user or org has changed then delete all the roles that match self.instances and create new ones.
        if str(self.instance.user.id) != self.cleaned_data['user'] or self.instance.org != self.cleaned_data['org']:
            self.instances.delete()
            return self.create()

        instance = None
        if not self.instance.course_id:
            # We are on the organization level course access role management page.
            self.instances.exclude(
                role__in=self.cleaned_data.get('roles', [])
            ).delete()

            for role in self.cleaned_data.get('roles', []):
                instance = get_or_create_course_access_role(
                    self.user,
                    user_id=self.cleaned_data['user'],
                    org=self.cleaned_data['org'],
                    role=role,
                )
        else:
            # We are on course level access roles page.
            self.instances.exclude(
                role__in=self.cleaned_data.get('roles', []),
                course_id__in=self.cleaned_data.get('course_ids', [])
            ).delete()

            for course_id in self.cleaned_data.get('course_ids', []):
                for role in self.cleaned_data.get('roles', []):
                    instance = get_or_create_course_access_role(
                        self.user,
                        user_id=self.cleaned_data['user'],
                        org=self.cleaned_data['org'],
                        role=role,
                        course_id=course_id,
                    )
        return instance
