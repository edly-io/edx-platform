"""
This file contains celery tasks for sending email
"""


import logging

from celery.exceptions import MaxRetriesExceededError
from celery.task import task
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from edx_ace import ace
from edx_ace.errors import RecoverableChannelDeliveryError
from edx_ace.message import Message
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.celery.task_utils import emulate_http_request

log = logging.getLogger('edx.celery.task')


@task(bind=True)
def send_activation_email(self, msg_string, site_id, from_address=None):
    """
    Sending an activation email to the user.
    """
    msg = Message.from_string(msg_string)

    max_retries = settings.RETRY_ACTIVATION_EMAIL_MAX_ATTEMPTS
    retries = self.request.retries

    from openedx.core.djangoapps.theming.helpers import get_current_site
    site_test = get_current_site()

    log.warning(" ---- ACTIVATION_EMAIL_FROM_ADDRESS ------- %s", configuration_helpers.get_value('ACTIVATION_EMAIL_FROM_ADDRESS'))
    log.warning(" ---- email_from_address ------- %s", configuration_helpers.get_value('email_from_address'))
    log.warning(" ---- DEFAULT_FROM_EMAIL ------- %s", settings.DEFAULT_FROM_EMAIL)
    log.warning(" ---- Site ID ------- %s", site_id)
    log.warning(" ---- Test Site ------- %s", site_test)
    log.warning(" ---- Test Site Config ------- %s", getattr(site_test, "configuration", None))
    log.warning(" ---- Site Configuration ------- %s", configuration_helpers.get_current_site_configuration())

    log.warning(" ---- From Address ------- %s", from_address)


    if from_address is None:
        from_address = configuration_helpers.get_value('ACTIVATION_EMAIL_FROM_ADDRESS') or (
            configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)
        )
    msg.options['from_address'] = from_address

    log.warning(" ---- From Address AFTER ------- %s", msg.options['from_address'])

    dest_addr = msg.recipient.email_address

    site = Site.objects.get(id=site_id)
    user = User.objects.get(username=msg.recipient.username)

    try:
        with emulate_http_request(site=site, user=user):
            ace.send(msg)
    except RecoverableChannelDeliveryError:
        log.info('Retrying sending email to user {dest_addr}, attempt # {attempt} of {max_attempts}'.format(
            dest_addr=dest_addr,
            attempt=retries,
            max_attempts=max_retries
        ))
        try:
            self.retry(countdown=settings.RETRY_ACTIVATION_EMAIL_TIMEOUT, max_retries=max_retries)
        except MaxRetriesExceededError:
            log.error(
                'Unable to send activation email to user from "%s" to "%s"',
                from_address,
                dest_addr,
                exc_info=True
            )
    except Exception:
        log.exception(
            'Unable to send activation email to user from "%s" to "%s"',
            from_address,
            dest_addr,
        )
        raise Exception
