from django.conf import settings
from django.db import models
from django import forms


# Backwards compatible settings.AUTH_USER_MODEL
USER_MODEL = getattr(settings, 'AUTH_USER_MODEL', 'auth.User')


class AdditionalRegistrationFields(models.Model):
    """
        This model contains two extra fields that will be saved when a user registers.
        The form that wraps this model is in the forms.py file.
        """
    user = models.OneToOneField(USER_MODEL, null=True, on_delete=models.SET_NULL)

    user_type = models.CharField(blank=False, max_length=8, verbose_name=b'User Type', choices=[
        (b'student', b'Student'),
        (b'staff', b'Staff')
    ])

    phone = models.CharField(blank=False, max_length=16, verbose_name=b'Phone')

    # school / organization name
    sch_org = models.CharField(blank=False, max_length=150, verbose_name=b'School/Organization')

    organization_type = models.CharField(blank=False, max_length=32, verbose_name=b'Organization Type', choices=[
        (b'corporate', b'Corporate'),
        (b'school district', b'School District'),
        (b'private school', b'Private School'),
        (b'higher ed', b'Higher ED'),
        (b'training', b'Training')
    ])
