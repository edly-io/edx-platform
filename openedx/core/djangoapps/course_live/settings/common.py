"""
Common settings for Live Video Conferencing Tools.
"""


def plugin_settings(settings):
    """
    Common settings for Live Video Conferencing Tools
    Set these variables in the Tutor Config or lms.yml for local testing
        ZOOM_BUTTON_GLOBAL_KEY: <ZOOM_BUTTON_GLOBAL_KEY>
        ZOOM_BUTTON_GLOBAL_SECRET: <ZOOM_BUTTON_GLOBAL_SECRET>
        ZOOM_BUTTON_GLOBAL_URL: <ZOOM_BUTTON_GLOBAL_URL>
        ZOOM_INSTRUCTOR_EMAIL: <ZOOM_INSTRUCTOR_EMAIL>
    """
    # zoom settings
    settings.ZOOM_BUTTON_GLOBAL_KEY = ""
    settings.ZOOM_BUTTON_GLOBAL_SECRET = ""
    settings.ZOOM_BUTTON_GLOBAL_URL = ""
    settings.ZOOM_INSTRUCTOR_EMAIL = ""
