# -*- coding: utf-8 -*-
from django.apps import AppConfig


class EdlyAppConfig(AppConfig):
    name = 'openedx.features.edly'

    def ready(self):
        import openedx.features.edly.signals # noqa
