"""
Authenitcation and Social Auth Pipeline methods for Colaraz's customizations
"""
import logging

from openedx.features.colaraz_features.models import (
    ColarazUserProfile,
    COLARAZ_DEFAULT_LEVEL_PERCENTAGE,
    COLARAZ_DEFAULT_LEVEL_TEXT,
)

LOGGER = logging.getLogger(__name__)

def store_id_token(request, response, user=None, *args, **kwargs):
    """
    This method is used in SOCIAL_AUTH_PIPELINE. It stores 'id_token' from the User's
    data sent by Auth Provider to request's 'session' object.
    """
    if user and response.has_key('id_token'):
        request.session['id_token'] = response['id_token']


def update_colaraz_profile(request, response, user=None, *args, **kargs):
    """
    Updates ColarazUserProfile using data sent from Colaraz Auth Provider
    """
    if user:
        try:
            instance, _ = ColarazUserProfile.objects.update_or_create(user=user, defaults={
                'site_identifier': str(response.get('companyInfo', {}).get('url', '')).lower(),
                'level_percentage': response.get('levelPercentage', COLARAZ_DEFAULT_LEVEL_PERCENTAGE),
                'level_text': response.get('levelText', COLARAZ_DEFAULT_LEVEL_TEXT),
            })
            request.session['user_site_identifier'] = instance.site_identifier
        except AttributeError:
            LOGGER.error('Data provided by auth-provider is not appropriate')
