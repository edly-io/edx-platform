"""
Django admin command to send reminder and due-dates emails.
"""
from logging import getLogger
from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = getLogger(__name__)


class Command(BaseCommand):
    """
    This command will check and send reminder and due-date emails to the enrolled users.
        $ ./manage.py lms send_due_date_and_reminder_emails
    """
    help = 'Command to check and send courses and events reminder and due date emails'

    def _run_command(self, name):
        try:
            call_command(name)
        except Exception as ex:
            logger.error("COMMAND_ERROR - Unable to run {} management command.".format(name))
            logger.error(ex)



    def handle(self, *args, **options):
        self._run_command('check_and_send_reminder_emails')
        self._run_command('check_and_send_due_dates_emails')
