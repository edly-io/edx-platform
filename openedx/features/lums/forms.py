"""
Utility functions for validating LUMS forms.
"""
from django import forms

from openedx.features.lums.models import UserProfileExtension


class UserProfileExtensionForm(forms.ModelForm):
    """
    The fields on this form are derived from the "UserProfileExtension" model in LUMS models.py.
    """
    def __init__(self, *args, **kwargs):
        super(UserProfileExtensionForm, self).__init__(*args, **kwargs)

        self.fields['province'].error_messages = {
            'required': u'Please provide the province value.',
            'invalid': u'Please provide a valid option.',
        }

    class Meta(object):
        model = UserProfileExtension
        fields = (
            'province',
        )
