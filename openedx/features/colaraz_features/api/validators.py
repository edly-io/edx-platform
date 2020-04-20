"""
Custom Validators for Colaraz API.
"""
import re

from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _


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

    regex = re.compile(host_re, re.IGNORECASE)
    message = _('Enter a valid domain.')
