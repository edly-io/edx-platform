"""
Production settings for Live Video Conferencing Tools.
"""


def plugin_settings(settings):
    """
    Production settings for Live Video Conferencing Tools
    """

    # zoom settings
    settings.ZOOM_BUTTON_GLOBAL_KEY = settings.ENV_TOKENS.get(
        "ZOOM_BUTTON_GLOBAL_KEY", settings.ZOOM_BUTTON_GLOBAL_KEY
    )
    settings.ZOOM_BUTTON_GLOBAL_SECRET = settings.ENV_TOKENS.get(
        "ZOOM_BUTTON_GLOBAL_SECRET", settings.ZOOM_BUTTON_GLOBAL_SECRET
    )
    settings.ZOOM_BUTTON_GLOBAL_URL = settings.ENV_TOKENS.get(
        "ZOOM_BUTTON_GLOBAL_URL", settings.ZOOM_BUTTON_GLOBAL_URL
    )
    settings.ZOOM_INSTRUCTOR_EMAIL = settings.ENV_TOKENS.get(
        "ZOOM_INSTRUCTOR_EMAIL", settings.ZOOM_INSTRUCTOR_EMAIL
    )
