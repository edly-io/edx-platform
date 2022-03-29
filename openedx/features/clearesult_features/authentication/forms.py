"""
Clearesult authentication Forms.
"""
from crum import get_current_request
from django import forms
from django.core.exceptions import ValidationError

from openedx.features.clearesult_features.models import ClearesultSiteConfiguration


class SiteSecurityCodeForm(forms.Form):
    security_code = forms.CharField(label='Verification Code', max_length=20, widget=forms.PasswordInput)

    def clean_security_code(self):
        site = get_current_request().site
        clearesult_site_config = ClearesultSiteConfiguration.current(site)
        if self.cleaned_data['security_code'] != clearesult_site_config.security_code:
            raise ValidationError('The verification code you provided is not valid. '
                                  'Please contact our team to obtain a valid code.')
