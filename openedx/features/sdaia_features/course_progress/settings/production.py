
"""Settings"""


def plugin_settings(settings):
    """
    Required Common settings
    """ 
    settings.SDAIA_DEFAULT_LANGUAGE_CODE = settings.ENV_TOKENS.get(
        'SDAIA_DEFAULT_LANGUAGE_CODE', settings.SDAIA_DEFAULT_LANGUAGE_CODE
    )
