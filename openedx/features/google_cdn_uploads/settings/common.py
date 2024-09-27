"""
Common settings for Messenger
"""


def plugin_settings(settings):
    """
    Common settings for uploading mp4
    """
    settings.MAKO_TEMPLATE_DIRS_BASE.append(
      settings.OPENEDX_ROOT / 'features' / 'google_cdn_uploads' / 'templates',
    )

    settings.STATICFILES_DIRS.append (
      settings.OPENEDX_ROOT / 'features' / 'google_cdn_uploads' / 'static',
    )
    print('CDN App - Setting Updated')
