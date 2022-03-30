"""
Utilities for user authentication.
"""
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse

from logging import getLogger

from openedx.features.clearesult_features.models import ClearesultUserSiteProfile, ClearesultSiteConfiguration

LOGGER = getLogger(__name__)


def _get_authentication_key_for_site(request):
    return 'authenticated-for-{}'.format(request.site.id)


def is_user_authenticated_for_site(request, allow_anonymous=True):
    # Somehow in database there are some hard-coded values being set for
    # a specific environment DEV, these values also include the domain part
    # which fails this authentication that's why putting this if check here
    # to ignore that scenerio
    if request.path.startswith('/asset'):
        return True

    is_already_authenticated = request.session.get(_get_authentication_key_for_site(request), False)

    if is_already_authenticated or (request.user.is_anonymous and allow_anonymous):
        return True

    if not allow_anonymous and request.user.is_anonymous:
        return False

    try:
        clearesult_site_config = ClearesultSiteConfiguration.current(request.site)
        previous_code = request.user.clearesult_site_profile.get(site=request.site).saved_security_code
        return previous_code == clearesult_site_config.security_code

    except ClearesultUserSiteProfile.DoesNotExist:
        return False


def authenticate_site_session(request):
    _, created = ClearesultUserSiteProfile.objects.update_or_create(
        user=request.user,
        site=request.site,
        defaults={
            'saved_security_code': request.POST['security_code']
        }
    )
    if created:
        LOGGER.info('Created a new clearesult user site profile for user {} and site {}'.format(
            request.user,
            request.site
        ))
    else:
        LOGGER.info('Updated clearesult user site profile for user {} and site {}'.format(
            request.user,
            request.site
        ))

    request.session[_get_authentication_key_for_site(request)] = True


def get_next_redirect_page_url(request):
    next_url = request.GET.get('next')
    if not next_url:
        next_url = '/dashboard'

    if (next_url in reverse('clearesult_features:continuing_education') or
        next_url in reverse('clearesult_features:participation_code')):
        next_url = '{url}?first_time=true'.format(url=next_url)

    return next_url
