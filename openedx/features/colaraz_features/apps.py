# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.apps import AppConfig


class ColarazFeaturesConfig(AppConfig):
    name = 'openedx.features.colaraz_features'

    def ready(self):
        super(ColarazFeaturesConfig, self).ready()
        from .signals import *  # pylint: disable=import-error,wildcard-import
