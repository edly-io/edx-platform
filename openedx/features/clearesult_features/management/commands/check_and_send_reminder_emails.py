"""
Django admin command to send reminder emails.
"""
from logging import getLogger
from django.core.management.base import BaseCommand
from openedx.features.clearesult_features.tasks import check_and_send_reminder_emails

logger = getLogger(__name__)

class Command(BaseCommand):
    """
    This command will check and send reminder emails to enrolled users for the course end date and event start date.
    Example usage:
        $ ./manage.py lms check_and_send_reminder_emails
    """
    help = 'Command to check and send courses and events reminder emails'

    def handle(self, *args, **options):
        logger.info("Reminder Emails TASK has been enqueued.")
        check_and_send_reminder_emails.delay()
