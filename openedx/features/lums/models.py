"""
Models for LUMS custom Information.
"""
from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import ugettext_lazy as _


class UserProfileExtension(models.Model):
    """
    This model is used to store additional custom profile fields.
    """

    PUNAJB = 'punjab'
    SINDH = 'sindh'
    KPK = 'kpk'
    BALOCHISTAN = 'balochistan'

    PROVINCE_CHOICES = (
        (PUNAJB, _('Punjab')),
        (SINDH, _('Sindh')),
        (KPK, _('Khyber Pakhtunkhwa')),
        (BALOCHISTAN, _('Balochistan')),
    )

    user = models.OneToOneField(
        User, related_name='profile_extension', on_delete=models.CASCADE
    )
    province = models.CharField(
        max_length=50, choices=PROVINCE_CHOICES, null=False, blank=False
    )
