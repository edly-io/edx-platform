"""
Serializers for colaraz application.
"""
from django import forms

from student.models import CourseAccessRole
from student.admin import CourseAccessRoleForm
from openedx.features.colaraz_features.helpers import get_user_organizations


class ColarazCourseAccessRoleForm(CourseAccessRoleForm):
    class Meta(object):
        model = CourseAccessRole
        fields = ('email', 'org', 'course_id', 'role', )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        super(ColarazCourseAccessRoleForm, self).__init__(*args, **kwargs)

        # Mark required fields
        self.fields['org'].required = True

    def clean(self):
        """
        Rename email field back to user.
        """
        cleaned_data = super(ColarazCourseAccessRoleForm, self).clean()
        if not self.errors:
            cleaned_data['user'] = cleaned_data['email']
            del cleaned_data['email']
        return cleaned_data

    def clean_org(self):
        """
        Clean and validate organization in the payload.
        """
        org = super(ColarazCourseAccessRoleForm, self).clean_org()
        if not self.user.is_staff:
            user_orgs = get_user_organizations(self.user)
            if org not in user_orgs:
                raise forms.ValidationError(
                    'User can only manage roles of it\'s own organization. Try {}'.format(' or '.join(
                        user_orgs
                    ))
                )
        return org

    def save(self, commit=True):
        self.instance.user = self.cleaned_data['user']
        return super(ColarazCourseAccessRoleForm, self).save(commit)
