"""
Custom Fields for colaraz API.
"""
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from openedx.features.colaraz_features.api.validators import (
    DomainValidator,
    SiteNameValidator,
)


class DomainField(serializers.CharField):
    default_error_messages = {
        'invalid': _('Enter a valid domain. e.g: example.com')
    }

    def __init__(self, **kwargs):
        super(DomainField, self).__init__(**kwargs)
        validator = DomainValidator(message=self.error_messages['invalid'])
        self.validators.append(validator)


class SiteNameField(serializers.CharField):
    default_error_messages = {
        'invalid': _('Enter a valid url domain without spaces and any special character. e.g: cambridge, lums')
    }

    def __init__(self, **kwargs):
        super(SiteNameField, self).__init__(**kwargs)
        validator = SiteNameValidator(message=self.error_messages['invalid'])
        self.validators.append(validator)
