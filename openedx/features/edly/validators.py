from logging import getLogger

from django.conf import settings
from django.utils.translation import ugettext as _

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.features.edly.constants import (
    ESSENTIALS,
    NUMBER_OF_COURSES,
    NUMBER_OF_REGISTERED_USERS,
)
from openedx.features.edly.models import (
    EdlyMultiSiteAccess,
    EdlySubOrganization,
)
from openedx.features.edly.utils import (
    create_edly_access_role,
    user_can_login_on_requested_edly_organization
)

logger = getLogger(__name__)


def is_edly_user_allowed_to_login(request, possibly_authenticated_user):
    """
    Check if the user is allowed to login on the current site.

    This method checks if the user has edly sub organization of current
    site in it's edly sub organizations list.

    Arguments:
        request (object): HTTP request object
        possibly_authenticated_user (User): User object trying to authenticate

    Returns:
        bool: Returns True if User has Edly Sub Organization Access Otherwise False.
    """

    if possibly_authenticated_user.is_superuser:
        return True

    try:
        edly_sub_org = request.site.edly_sub_org_for_lms
    except EdlySubOrganization.DoesNotExist:
        logger.error('Edly sub organization does not exist for site %s.' % request.site)
        return False

    try:
        EdlyMultiSiteAccess.objects.get(user=possibly_authenticated_user, sub_org=edly_sub_org)
    except EdlyMultiSiteAccess.DoesNotExist:
        logger.warning('User %s has no edly multisite user for site %s.' % (possibly_authenticated_user.email, request.site))
        return False

    return True


def is_edly_user_allowed_to_login_with_social_auth(request, user):
    """
    Check if the user is allowed to login on the current site with social auth.

    Arguments:
        request (object): HTTP request object
        user: User object trying to authenticate

    Returns:
        bool: Returns True if User can login to site otherwise False.
    """

    if not is_edly_user_allowed_to_login(request, user):
        if user_can_login_on_requested_edly_organization(request, user):
            create_edly_access_role(request, user)
        else:
            logger.warning('User %s is not allowed to login for site %s.' % (user.email, request.site))
            return False

    return True


def is_courses_limit_reached_for_plan():
    """
    Checks if the limit for the current site for number of courses is reached.
    """
    site_config = configuration_helpers.get_current_site_configuration()
    current_plan = site_config.get_value('DJANGO_SETTINGS_OVERRIDE').get('CURRENT_PLAN', ESSENTIALS)
    plan_features = settings.PLAN_FEATURES.get(current_plan)

    courses_count = CourseOverview.get_all_courses(orgs=configuration_helpers.get_current_site_orgs()).count()

    if courses_count >= plan_features.get(NUMBER_OF_COURSES):
        return True

    return False


def get_subscription_limit(edly_sub_org, current_plan=None):
    """
    Checks if the limit for the current site for number of registered users is reached.
    """
    site_config = configuration_helpers.get_current_site_configuration()
    if not current_plan and site_config:
        current_plan = site_config.get_value('DJANGO_SETTINGS_OVERRIDE', {}).get('CURRENT_PLAN', ESSENTIALS)

    plan_features = settings.PLAN_FEATURES.get(current_plan)
    registration_limit = plan_features.get(NUMBER_OF_REGISTERED_USERS)

    user_records_count = EdlyMultiSiteAccess.objects.filter(sub_org=edly_sub_org).count()

    return registration_limit - user_records_count


def handle_subscription_limit(remaining_limit):
    """
    Returns appropriate errors if the limit has reached.
    """
    errors = {}
    if remaining_limit <= 0:
        errors['email'] = [{"user_message": _(
            u"The maximum users limit for your plan has reached. "
            u"Please upgrade your plan."
        )}]

    return errors
