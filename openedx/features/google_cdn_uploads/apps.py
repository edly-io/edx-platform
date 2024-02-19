"""
Meta Translations App Config
"""
from django.apps import AppConfig
from edx_django_utils.plugins import PluginURLs, PluginSettings
from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType

class GoogleCDNUploadsConfig(AppConfig):
    name = 'openedx.features.google_cdn_uploads'
    plugin_app = {
        PluginURLs.CONFIG: {
            ProjectType.CMS: {
                    PluginURLs.NAMESPACE: 'google_cdn_uploads',
                    PluginURLs.REGEX: '^google_cdn_uploads/',
                    PluginURLs.RELATIVE_PATH: 'urls',
                },
        },
        PluginSettings.CONFIG: {
            ProjectType.CMS: {
                SettingsType.COMMON: {PluginSettings.RELATIVE_PATH: 'settings.common'},
            },
        }
    }

