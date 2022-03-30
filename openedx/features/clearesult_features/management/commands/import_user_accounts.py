"""
Django admin command to create user accounts and their profiles from a given CSV file.
"""
from csv import DictWriter, Error
from logging import getLogger

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.utils.crypto import get_random_string
from social_django.models import UserSocialAuth

from openedx.features.clearesult_features.auth_backend import ClearesultAzureADOAuth2
from openedx.features.clearesult_features.instructor_reports.utils import get_credit_provider_by_short_code
from openedx.features.clearesult_features.models import ClearesultUserProfile, UserCreditsProfile
from openedx.features.clearesult_features.utils import get_csv_file_control
from student.models import UserProfile

logger = getLogger(__name__)
CREATION_SUCCESSFUL = 'CREATED'
CREATION_FAILED = 'FAILED'
UPDATE_SUCCESSFUL = 'UPDATED'
DUPLICATE_ENTRY = 'DUPLICATE EMAIL'


class Command(BaseCommand):
    """
    This command attempts to create user accounts, their profiles and their social auth accounts.
    Example usage:
        $ ./manage.py lms import_user_accounts '/tmp/user_accounts.csv'

    You will have to put your file some where in the server using "scp" utility, then you will give
    it's absolute path in the argument of this command.

    This command assusmes that your CSV file will contain the following headers

    Last Name, First Name, Email, Username, Job Title, Street Address, City, State/Province,
    Postal Code, Primary Phone, BPI, NATE, RESNET HERS Rater, AIA, AEE, LEED

    BPI, NATE, RESNET HERS Rater, AIA, AEE, LEED are the short codes for credit providers.
    and their values are the Continuing Education User IDs for each certain credit provider.
    Our file contains which credit providers, we will know that through django settings variable
    named "CLEARESULT_CREDIT_PROVIDERS"

    Before running this command you will have to add your credit providers through django admin.
    So that this command can save the Continuing education user IDs for those credit providers.
    """
    help = 'Command to create user accounts, their profiles (edx, clearesult) and social auth accounts.'

    def add_arguments(self, parser):
        parser.add_argument(
            'file_path',
            type=str,
            help='The absolute path of csv file which contains the user accounts details.'
        )

    def handle(self, *args, **options):
        file_path = options['file_path']
        file_controller = get_csv_file_control(file_path)
        if not file_controller:
            logger.info('Unable to get file control.')
            return

        traversed_emails = []
        output_file_rows = []
        total_count, updated_count, created_count, failure_count, duplicate_count = 0, 0, 0, 0, 0
        try:
            for row in file_controller['csv_reader']:
                total_count = total_count + 1
                row['Status'] = ''
                row['Error'] = ''
                if not _is_email_traversed(traversed_emails, row):
                    try:
                        with transaction.atomic():
                            edx_user = _create_or_update_edx_users(row)
                            _create_or_update_edx_user_profile(edx_user, row)
                            _create_or_update_clearesult_user_profile(edx_user, row)
                            _create_or_update_user_credits_profile(edx_user, row)
                            created = _create_or_update_user_social_auth_accounts(edx_user)
                            if created:
                                created_count = created_count + 1
                            else:
                                updated_count = updated_count + 1
                    except IntegrityError as error:
                        logger.exception('Error while creating/updating ({}) user and its '
                                        'profiles with the following errors {}.'.format(row.get('Email').lower(), error))
                        failure_count = failure_count + 1
                        row['Status'] = CREATION_FAILED
                        row['Error'] = error
                else:
                    row['Status'] = DUPLICATE_ENTRY
                    row['Error'] = 'This email has already been traversed.'
                    duplicate_count = duplicate_count + 1

                output_file_rows.append(row)
        except Error as err:
            logger.exception('Error while traversing {} file content with following error {}.'
                             .format(file_path, err))

        _log_final_report(total_count, created_count, updated_count, failure_count, duplicate_count)
        file_controller['csv_file'].close()
        _write_status_on_csv_file(file_path, output_file_rows)


def _is_email_traversed(traversed_emails, row):
    email = row.get('Email').lower()
    if  email in traversed_emails:
        return True
    else:
        traversed_emails.append(email)
        return False


def _create_or_update_edx_users(user_info):
    """
    Creates/updates the edx users.

    Arguements:
        user_info (dict): Dict containing the values of single row of csv file
    """
    user_data = {
        'email': user_info.get('Email').lower(),
        'first_name': user_info.get('First Name'),
        'last_name': user_info.get('Last Name'),
        'username': user_info.get('Username'),
        'is_active': True
    }
    edx_user, created = User.objects.update_or_create(username=user_info.get('Username'), defaults=user_data)
    if created:
        edx_user.set_password(get_random_string())
        edx_user.save()
        logger.info('{} user has been created.'.format(edx_user.username))
    else:
        logger.info('{} user has been updated.'.format(edx_user.username))

    return edx_user


def _create_or_update_edx_user_profile(edx_user, user_info):
    """
    Creates/updates the edx user profile.

    Arguements:
        edx_user (User): Django User
        user_info (dict): Dict containing the values of single row of csv file
    """
    profile_data = {
        'name': '{} {}'.format(edx_user.first_name, edx_user.last_name),
        'city': user_info.get('City'),
        'location': user_info.get('Street Address'),
        'phone_number': user_info.get('Primary Phone')
    }
    _, created = UserProfile.objects.update_or_create(user=edx_user, defaults=profile_data)
    if created:
        logger.info('{} edx profile has been created.'.format(edx_user.username))
    else:
        logger.info('{} edx profile has been updated.'.format(edx_user.username))


def _create_or_update_clearesult_user_profile(edx_user, user_info):
    """
    Creates/updates the clearesult user profile.

    Arguements:
        edx_user (User): Django User
        user_info (dict): Dict containing the values of single row of csv file
    """
    clearesult_profile_data = {
        'company': user_info.get('Company'),
        'state_or_province': user_info.get('State/Province'),
        'postal_code': user_info.get('Postal Code')
    }
    _, created = ClearesultUserProfile.objects.update_or_create(user=edx_user, defaults=clearesult_profile_data)
    if created:
        logger.info('{} clearesult profile has been created.'.format(edx_user.username))
        user_info['Status'] = CREATION_SUCCESSFUL
    else:
        logger.info('{} clearesult profile has been updated.'.format(edx_user.username))
        user_info['Status'] = UPDATE_SUCCESSFUL


def _create_or_update_user_credits_profile(edx_user, user_info):
    """
    Creates/updates the user credits profile.

    Performs operation only for those users who have some distinct value of
    continueing education ID for the credit providers available in the platform.

    Arguements:
        edx_user (User): Django User
        user_info (dict): Dict containing the values of single row of csv file
    """
    possible_credit_providers = getattr(settings, 'CLEARESULT_CREDIT_PROVIDERS', [])

    for possible_credit_provider in possible_credit_providers:
        if possible_credit_provider in user_info.keys() and not user_info.get(possible_credit_provider) == '':
            credit_provider = get_credit_provider_by_short_code(possible_credit_provider)
            if credit_provider:
                data = {
                    'credit_id': user_info.get(possible_credit_provider)
                }
                _, created = UserCreditsProfile.objects.update_or_create(user=edx_user,
                                                                         credit_type=credit_provider,
                                                                         defaults=data)
                if created:
                    logger.info('{} account ID for {} credit provider of user {} has been added.'.format(
                        user_info.get(possible_credit_provider),
                        possible_credit_provider,
                        edx_user.email
                    ))
                else:
                    logger.info('{} account ID for {} credit provider of user {} has been updated.'.format(
                        user_info.get(possible_credit_provider),
                        possible_credit_provider,
                        edx_user.email
                    ))


def _create_or_update_user_social_auth_accounts(edx_user):
    """
    Creates/updates the social auth accounts of the given edx users.

    Arguements:
        edx_user (User): Django User
    """
    data = {
        'uid': edx_user.email,
        'provider': ClearesultAzureADOAuth2.name,
        'user': edx_user
    }
    _, created = UserSocialAuth.objects.update_or_create(user=edx_user, provider=ClearesultAzureADOAuth2.name,
                                                         defaults=data)

    if created:
        logger.info('User socail auth for user {} has been created.'.format(edx_user.email))
    else:
        logger.info('User socail auth for user {} has been updated.'.format(edx_user.email))

    return created


def _write_status_on_csv_file(file_path, output_file_rows):
    """
    Writes the output data on the given file.
    """
    try:
        with open(file_path, 'w') as csv_file:
            if output_file_rows:
                writer = DictWriter(csv_file, fieldnames=output_file_rows[0].keys())
                writer.writeheader()
                for row in output_file_rows:
                    writer.writerow(row)
    except IOError as error:
        logger.exception('(file_path) --- {}'.format(file_path, error.strerror))


def _log_final_report(total_count, created_count, updated_count, failure_count, duplicate_count):
    logger.info('\n\n\n')
    logger.info('Total number of attempts to create/update users\' accounts and their profiles: {}'.format(total_count))
    logger.info('Total number of newly created accounts and profiles: {}'.format(created_count))
    logger.info('Total number of updated accounts and profiles: {}'.format(updated_count))
    logger.info(
        'Total number of failures which were encountered while creating/updating user accounts and profiles: {}'.format(
            failure_count
        )
    )
    logger.info('Total unique entries: {}'.format(total_count - duplicate_count))
    logger.info('Total duplicate entries: {}'.format(duplicate_count))
