"""
Authenitcation and Social Auth Pipeline methods for Colaraz's customizations
"""
import logging

from openedx.features.colaraz_features.helpers import make_user_lms_admin
from openedx.features.colaraz_features.models import (
    ColarazUserProfile,
    DEFAULT_JOB_TITLE,
    DEFAULT_PROFILE_STRENGTH_COLOR,
    DEFAULT_PROFILE_STRENGTH_TITLE,
    DEFAULT_PROFILE_STRENGTH_WIDTH,
)

LOGGER = logging.getLogger(__name__)


def store_id_token(request, response, user=None, *args, **kwargs):
    """
    This method is used in SOCIAL_AUTH_PIPELINE. It stores 'id_token' from the User's
    data sent by Auth Provider to request's 'session' object.
    """
    if user and response.has_key('id_token'):
        request.session['id_token'] = response['id_token']


def update_site_admin(response, user=None, *args, **kwargs):
    if user and response.get('role') == 'Company Admin':
        primary_org = str(response.get('companyInfo', {}).get('url', '')).lower()
        make_user_lms_admin(user, primary_org)


def update_colaraz_profile(request, response, user=None, *args, **kargs):
    """
    Updates ColarazUserProfile using data sent from Colaraz Auth Provider
    """
    if user:
        try:
            profile_strength = response.get('profileStrength', {})
            instance, _ = ColarazUserProfile.objects.update_or_create(user=user, defaults={
                'elgg_id': response.get('elggId'),
                'job_title': response.get('jobTitle', DEFAULT_JOB_TITLE),
                'profile_image_url': response.get('profilePicture'),
                'profile_strength_title': profile_strength.get('title', DEFAULT_PROFILE_STRENGTH_TITLE),
                'profile_strength_color': profile_strength.get('color', DEFAULT_PROFILE_STRENGTH_COLOR),
                'profile_strength_width': profile_strength.get('width', DEFAULT_PROFILE_STRENGTH_WIDTH),
                'site_identifier': str(response.get('companyInfo', {}).get('url', '')).lower(),
            })
            request.session['user_site_identifier'] = instance.site_identifier
        except AttributeError:
            LOGGER.error('Data provided by auth-provider is not appropriate')
