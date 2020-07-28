"""
Register redhouse registration app models for django admin
"""

from django.contrib import admin

from openedx.features.redhouse_features.registration.models import AdditionalRegistrationFields


admin.site.register(AdditionalRegistrationFields)
