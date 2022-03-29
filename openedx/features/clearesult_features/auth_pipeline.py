"""
Auth pipeline to modify authentication behavior
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.shortcuts import redirect
from social_django.models import UserSocialAuth

from third_party_auth import pipeline

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.features.clearesult_features.auth_backend import ClearesultAzureADOAuth2
from openedx.features.clearesult_features.models import ClearesultUserProfile, ClearesultSiteConfiguration
from  openedx.features.clearesult_features.utils import (
    add_user_to_site_default_group, set_user_first_and_last_name,
    get_site_from_site_identifier
)
from openedx.features.clearesult_features.tasks import update_magento_user_info_from_drupal


logger = logging.getLogger(__name__)
User = get_user_model()


def replace_old_clearesult_app_uid(backend, uid, details, response, *args, **kwargs):
    """
    When uid of the user social auth account available in edX and the user account coming from
    IdP doesn't match but they have same email address, get edX user account using email
    instead of uid and set it's uid value to the uid value that is coming from IdP so that
    both of these accounts are linked to each other.
    """
    if backend.name == ClearesultAzureADOAuth2.name:
        email = details.get('email').lower()
        if email:
            try:
                user = User.objects.get(email=email)
                social_auth_user = UserSocialAuth.objects.get(user=user, provider=backend.name)
                if social_auth_user.uid != uid:
                    logger.info('User with email {} is already linked.'.format(email))
                    social_auth_user.uid = uid
                    social_auth_user.save()
                    logger.info('User with email {} has been updated with new uid'.format(email))
            except (User.DoesNotExist, UserSocialAuth.DoesNotExist):
                logger.info('Could not find user or social_auth_user with email: {}.'.format(email))
        else:
            logger.info('Could not fetch email from facebook against uid: {}.'.format(uid))


def redirect_to_participation_code(request, user=None, *_, **__):
    """
    Redirect a new registered user to "Participation Code" page.
    """
    clearesult_site_config = ClearesultSiteConfiguration.current(request.site)
    if clearesult_site_config.participation_code_required:
        if user and not user.clearesult_profile.get_extension_value('has_visited_participation_code', False):
            user.clearesult_profile.set_extension_value('has_visited_participation_code', True)
            return redirect(reverse('clearesult_features:participation_code'))
    else:
        if user and not user.clearesult_profile.get_extension_value('has_visited_continuing_education_form', False):
            user.clearesult_profile.set_extension_value('has_visited_continuing_education_form', True)
            return redirect(reverse('clearesult_features:continuing_education'))


def update_clearesult_user_and_profile(request, response, user=None, *args, **kwargs):
    """
    Updates clearesult user and his profile data coming from Azure AD B2C OAuth provider.
    """
    if user:
        try:
            full_name = response.get('name', 'N/A N/A').split(' ')
            set_user_first_and_last_name(user, full_name)
            instance, created = ClearesultUserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'site_identifiers': response.get('jobTitle', ''),
                    'company': response.get('extension_Client', ''),
                    'state_or_province': response.get('state', ''),
                    'postal_code': response.get('postalCode', '')
                }
            )
            if created:
                logger.info('Success: The clearesult user and his profile have been created for user {}.'.format(user.email))
            else:
                logger.info('Success: The clearesult user and his profile have been updated for user {}.'.format(user.email))
            # * Drupal team is sending site identifier information in jobTitle
            _set_user_site_identifiers(request, instance, response.get('jobTitle', ''))

            update_magento_user_info_from_drupal.delay(
                instance.user.email,
                configuration_helpers.get_value('MAGENTO_BASE_API_URL', settings.MAGENTO_BASE_API_URL),
                configuration_helpers.get_value('MAGENTO_LMS_INTEGRATION_TOKEN', settings.MAGENTO_LMS_INTEGRATION_TOKEN)
            )

        except AttributeError:
            logger.error('Failed: Could not create/update clearesult user and his profile.')


def _set_user_site_identifiers(request, clearesult_user_profile, incoming_site_identifiers):
    if incoming_site_identifiers.strip() != '':
        incoming_site_identifiers_list = incoming_site_identifiers.split(',')
        if len(incoming_site_identifiers_list) > 0:
            clearesult_user_profile.site_identifiers = incoming_site_identifiers
            clearesult_user_profile.save()
            for site_identifier in incoming_site_identifiers_list:
                site = get_site_from_site_identifier(clearesult_user_profile.user, site_identifier)
                if site:
                    add_user_to_site_default_group(request, clearesult_user_profile.user, site)


def block_user_to_access_restricted_site(request, response, user=None, *args, **kwargs):
    """
    Third party pipeline which will be executed at the very beginning of authentication.
    So that if the user is not eligible, don't waste anything extra
    """
    site_name = '-'.join(request.site.name.split('-')[:-1]).rstrip()
    if not site_name in response.get('jobTitle', ''):
        logger.info('Failed: user is not eligible to be logged in this site.')
        return HttpResponseRedirect(reverse('root'))
