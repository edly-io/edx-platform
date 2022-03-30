"""
Django admin command to revert the impact
of "import_user_accounts" management command.
"""
from csv import Error
from logging import getLogger

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from openedx.features.clearesult_features.utils import get_csv_file_control

logger = getLogger(__name__)


class Command(BaseCommand):
    """
    This command reverts the impacts of "import_user_accounts" management command.
    Example usage:
        $ ./manage.py lms revert_import_user_accounts '/tmp/user_accounts.csv'

    We will be using the same CSV file that we used with "import_user_accounts"
    but this time we will only read emails from the file to find the users'
    accounts in our database and then we will delete their accounts, profiles and
    social auth accounts. Because of cascade deletion we are only deleting django user
    account, deletion of profiles and social auth accounts will be handled automatically.
    """
    help = 'Command revert the impact of "import_user_accounts"'

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

        total_count, deleted_count, failure_count = 0, 0, 0
        try:
            for row in file_controller['csv_reader']:
                try:
                    total_count = total_count + 1
                    User.objects.get(email=row.get('Email')).delete()
                    deleted_count = deleted_count + 1
                    logger.info('User and his profiles  with email {} have been deleted.'.format(row.get('Email')))
                except User.DoesNotExist:
                    failure_count = failure_count + 1
                    logger.info('Failure while deleting user ({}) as user does not exist.'.format(row.get('Email')))
        except Error as err:
            logger.exception('Error while traversing {} file content with following error {}.'
                             .format(file_path, err))

        _log_final_report(total_count, deleted_count, failure_count)
        file_controller['csv_file'].close()


def _log_final_report(total_count, deleted_count, failure_count):
    logger.info('\n\n\n')
    logger.info('Total number of attemps to delete users\' accounts and their profiles: {}.'.format(total_count))
    logger.info('Total number of successful deletions: {}.'.format(deleted_count))
    logger.info(
        'Total number of failures which were encountered while deleting user accounts and their profiles: {}.'.format(
            failure_count
        )
    )
