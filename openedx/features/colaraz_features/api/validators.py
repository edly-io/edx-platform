"""
Custom Validators for Colaraz API.
"""
import re

from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _


class DomainValidator(RegexValidator):
    ul = '\u00a1-\uffff'  # unicode letters range (must not be a raw string)

    # Hostname e.g. example in example.com
    hostname_re = r'[a-z' + ul + r'0-9](?:[a-z' + ul + r'0-9-]{0,61}[a-z' + ul + r'0-9])?'
    # Domain e.g. .com in example.com
    domain_re = r'(?:\.(?!-)[a-z' + ul + r'0-9-]{1,63}(?<!-))+'
    host_re = '^(' + hostname_re + domain_re + '|localhost)$'

    regex = re.compile(host_re, re.IGNORECASE)
    message = _('Enter a valid domain.')
