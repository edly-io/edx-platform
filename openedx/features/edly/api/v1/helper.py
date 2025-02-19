from django.db.models import Count
from openedx.features.edly.models import EdlyMultiSiteAccess

def get_users_for_site(sub_org):
    """
    Get users for a site, excluding those linked with multiple sites.
    """
    users = EdlyMultiSiteAccess.objects.filter(
        sub_org=sub_org
    ).values_list('user', flat=True)

    users_obj = EdlyMultiSiteAccess.objects.filter(
        user__in=users
    ).values('user', 'user__username', 'user__email').annotate(
        site_count=Count('id')
    )
    return users_obj
