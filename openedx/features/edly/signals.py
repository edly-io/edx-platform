from django.contrib.auth.models import User
from django.dispatch import receiver
from django.db.models.signals import post_save, pre_save
from django.utils import timezone

from openedx.features.edly.models import PasswordChange, PasswordHistory

@receiver(post_save, sender=User)
def create_user_handler(sender, instance, created, **kwargs):
    """
    When the user is created, create a password change record.
    """
    if not created:
        return
    
    now = timezone.now()
    PasswordChange.objects.create(user=instance, last_changed=now)
    PasswordHistory.objects.create(user=instance, created=now, password=instance.password)
    PasswordHistory.objects.delete_expired(instance)

@receiver(pre_save, sender=User)
def change_password_handler(sender, instance, **kwargs):
    """
    Checks if the user changed password
    contrib/auth/base_user.py sets _password in set_password()
    """
    if instance._password is None:
        return

    try:
        User.objects.get(id=instance.id)
    except User.DoesNotExist:
        return

    record, _ign = PasswordChange.objects.get_or_create(user=instance)
    record.last_changed = timezone.now()
    record.save()

    PasswordHistory.objects.create(user=instance, created=timezone.now(), password=instance.password)
    PasswordHistory.objects.delete_expired(instance)
    instance._has_not_previous_password = False
