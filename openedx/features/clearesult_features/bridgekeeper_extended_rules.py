"""
Contains extended rules of bridgekeeper.
"""
from bridgekeeper.rules import blanket_rule
from crum import get_current_request

from openedx.features.clearesult_features.models import ClearesultLocalAdmin


@blanket_rule
def is_local_admin(user):
    """
    Check that this user is local admin of current site or not.
    """
    request = get_current_request()
    return ClearesultLocalAdmin.objects.filter(user=user, site=request.site).exists()
