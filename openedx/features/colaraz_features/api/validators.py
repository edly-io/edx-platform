"""
Custom Validators for Colaraz API.
"""
import re

from django.conf import settings
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _
from rest_framework import authentication, exceptions
# For local testing, you will have to import
# from oauth2_provider.models import AccessToken
# We are doing it on the basis of settings.DEBUG flag in the imports section.
if settings.DEBUG == True:
    from oauth2_provider.models import AccessToken
else:
    from provider.oauth2.models import AccessToken


class DomainValidator(RegexValidator):
    """
    Domain name validation logic is contained withing this class.

    Domain names must only use the letters a to z, the numbers 0 to 9, and the hyphen (-) character.
    If the hyphen character is used in a domain name, it cannot be the first character in the name.
    For example, -example.com would not be allowed, but ex-ample.com would be.
    """
    # Hostname e.g. example in example.com
    hostname_re = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    # Domain e.g. .com in example.com
    domain_re = r'(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+'
    host_re = '^(' + hostname_re + domain_re + '|localhost)$'

    regex = re.compile(host_re)
    message = _('Enter a valid domain without spaces, capital letters and any special character. e.g: example.com')


class SiteNameValidator(RegexValidator):
    """
    Allows only a-z and 0-9.
    """
    # Hostname e.g. example in example.com
    hostname_re = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    host_re = '^(' + hostname_re + '|localhost)$'

    regex = re.compile(host_re)
    message = _('Enter a valid url domain without spaces, capital letters and any special character. e.g: cambridge, lums')


class TokenBasedAuthentication(authentication.BaseAuthentication):
    """
    Class to authenticate user via auth token passed in url as a parameter.
    """

    def authenticate(self, request):
        token = request.GET.get('token')
        if token:
            try:
                user = AccessToken.objects.get(token=token).user
                return user, None
            except AccessToken.DoesNotExist:
                raise exceptions.AuthenticationFailed(_('Invalid Token.'))
        else:
            raise exceptions.AuthenticationFailed(_('Access token required in url as query parameter'))
