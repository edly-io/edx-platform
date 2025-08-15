from django.db import models
from django.contrib.auth.hashers import identify_hasher
from django.conf import settings
import logging
logger = logging.getLogger(__name__)

class PasswordHistoryManager(models.Manager):
    config = getattr(settings, 'PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG', {})
    default_offset = config.get('PASSWORD_ROTATE_HISTORY_COUNT', 4)


    def delete_expired(self, user, offset=None):
        """
        Deletes expired password history entries from the database(s).

        :arg user: A :class:`~django.contrib.auth.models.User` instance.
        :arg int offset: A number specifying how much entries are to be kept
              in the user's password history. Defaults
              to :py:attr:`~settings.PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG.PASSWORD_ROTATE_HISTORY_COUNT`.
        """
        if not offset:
            offset = self.default_offset
        qs = self.filter(user=user)
        if qs.count() > offset:
            entry = qs[offset:offset + 1].get()
            qs.filter(created__lte=entry.created).delete()

    def check_password(self, user, raw_password):
        """
        Compares a raw (UNENCRYPTED!!!) password to entries in the users's
        password history.

        Args:
            user: A User instance
            raw_password: Unencrypted password string

        Returns:
            bool: False if password has been used before, True if not
        """
        try:
            if user and user.check_password(raw_password):
                return False

            entries = self.filter(user=user).all()[:self.default_offset]
            for entry in entries:
                if not entry.password:
                    continue

                try:
                    hasher = identify_hasher(entry.password)
                    if hasher.verify(raw_password, entry.password):
                        return False
                except ValueError as e:
                    logger.warning(
                        "Invalid password hash for user %s: %s", 
                        user.id, 
                        str(e)
                    )
                    continue

            return True

        except Exception as e:
            logger.error(
                "Error checking password history for user %s: %s",
                user.id if user else 'None',
                str(e)
            )
            return True
