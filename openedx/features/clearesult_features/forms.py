"""
Clearesult Forms.
"""

from django import forms
from openedx.features.clearesult_features.models import (
    ClearesultCourseCredit,
    UserCreditsProfile
)


class UserCreditsProfileAdminForm(forms.ModelForm):
    class Meta:
        model = UserCreditsProfile
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super(UserCreditsProfileAdminForm, self).__init__(*args, **kwargs)
        if 'earned_course_credits' in self.initial:
            self.fields['earned_course_credits'].queryset = ClearesultCourseCredit.objects.filter(
                credit_type=self.initial['credit_type']
            )
