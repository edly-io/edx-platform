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
from openedx.features.clearesult_features.auth_backend import ClearesultAzureADOAuth2
from openedx.features.clearesult_features.models import ClearesultUserProfile
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


def redirect_to_continuing_education(new_association, auth_entry, *_, **__):
    """
    Redirect a new registered user to "Continuing Education" page.
    """
    if new_association and auth_entry == pipeline.AUTH_ENTRY_REGISTER:
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
                    'job_title': response.get('jobTitle', ''),
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
        incoming_site_identifiers = incoming_site_identifiers.split(',')
        if len(incoming_site_identifiers) > 0:
            clearesult_user_profile.set_extension_value('site_identifier', incoming_site_identifiers)
            for site_identifier in incoming_site_identifiers:
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
