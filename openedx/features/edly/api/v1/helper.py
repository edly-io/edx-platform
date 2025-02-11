from django.db.models import Count
from openedx.features.edly.models import EdlyMultiSiteAccess

def get_users_for_site(sub_org):
    """
    Get users for a site, excluding those linked with multiple sites.
    """
    # edly_sub_org = EdlySubOrganization.objects.get(lms_site=site)
    users_obj = EdlyMultiSiteAccess.objects.filter(
        sub_org=sub_org
    ).values('user', 'user__username', 'user__email').annotate(
        site_count=Count('sub_org', distinct=True)
    )
    return users_obj
