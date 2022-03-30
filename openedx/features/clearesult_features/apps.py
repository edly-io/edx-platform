# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from django.apps import AppConfig


class ClearesultFeaturesConfig(AppConfig):
    name = 'openedx.features.clearesult_features'

    def ready(self):
        from . import signals  # pylint: disable=unused-import
        from . import tasks  # pylint: disable=unused-import
