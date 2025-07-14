"""
ACE message types for the edly app.
"""

from openedx.core.djangoapps.ace_common.message import BaseMessageType


class OTPMessage(BaseMessageType):
    def __init__(self, *args, **kwargs):
        super(OTPMessage, self).__init__(*args, **kwargs)

        self.options['transactional'] = True
