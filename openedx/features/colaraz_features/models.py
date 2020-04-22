
"""
Django models for colaraz_features app.
"""
from __future__ import unicode_literals

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


COLARAZ_DEFAULT_LEVEL_PERCENTAGE = 0
COLARAZ_DEFAULT_LEVEL_TEXT = 'Beginner'

class ColarazUserProfile(models.Model):
    """
    Represents colaraz user profile.

    Fields:
        site_identifier: It represents the starting part of domain which identifies domain e.g: in
            'abc.courses.colaraz.com' the identifier will be 'abc'
        level_percentage: Colaraz has a sidebar where it shows a profile strength progress bar
        level_text: Colaraz has a sidebar where it shows a profile strength display text e.g 'beginner'
    """
    class Meta:
        app_label = 'colaraz_features'
        verbose_name_plural = 'Colaraz user profiles'

    user = models.OneToOneField(User, unique=True, db_index=True,
                                related_name='colaraz_profile', on_delete=models.CASCADE)
    site_identifier = models.CharField(max_length=100)
    level_percentage = models.IntegerField(validators=[MinValueValidator(0), MaxValueValidator(100)], 
                                            default=COLARAZ_DEFAULT_LEVEL_PERCENTAGE)
    level_text = models.CharField(max_length=100, default=COLARAZ_DEFAULT_LEVEL_TEXT)
