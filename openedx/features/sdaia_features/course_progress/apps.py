"""
Progress Updates App Config
"""

from django.apps import AppConfig
from edx_django_utils.plugins import PluginURLs, PluginSettings
from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType


class CourseProgressConfig(AppConfig):
    name = "openedx.features.sdaia_features.course_progress"

    plugin_app = {
        PluginURLs.CONFIG: {
            ProjectType.LMS: {
                PluginURLs.NAMESPACE: "course_progress",
                PluginURLs.REGEX: r"^sdaia",
                PluginURLs.RELATIVE_PATH: "urls",
            }
        },
        PluginSettings.CONFIG: {
            ProjectType.LMS: {
                SettingsType.COMMON: {PluginSettings.RELATIVE_PATH: "settings.common"},
            }
        },
    }

    def ready(self):
        from . import signals  # pylint: disable=unused-import
