"""
Configure the django app
"""
from django.apps import AppConfig
from edx_django_utils.plugins import PluginURLs

from openedx.core.djangoapps.plugins.constants import ProjectType


class CourseLiveConfig(AppConfig):
    """
    Configuration class for Course Live.
    """

    name = "openedx.core.djangoapps.course_live"

    plugin_app = {
        PluginURLs.CONFIG: {
            ProjectType.LMS: {
                PluginURLs.NAMESPACE: '',
                PluginURLs.REGEX: r'^api/course_live/',
                PluginURLs.RELATIVE_PATH: 'urls',
            },
            ProjectType.CMS: {
                PluginURLs.NAMESPACE: '',
                PluginURLs.REGEX: r'^api/course_live/',
                PluginURLs.RELATIVE_PATH: 'urls',
            },
            'settings_config': {
                'lms.djangoapp': {
                    'common': {'relative_path': 'settings.common'},
                    'production': {'relative_path': 'settings.production'},
                },
                'cms.djangoapp': {
                    'common': {'relative_path': 'settings.common'},
                    'production': {'relative_path': 'settings.production'},
                },
            },
        }
    }
