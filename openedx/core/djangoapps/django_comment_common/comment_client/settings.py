# lint-amnesty, pylint: disable=cyclic-import, missing-module-docstring
from django.conf import settings

if hasattr(settings, "COMMENTS_SERVICE_URL"):
    SERVICE_HOST = settings.COMMENTS_SERVICE_URL
else:
    SERVICE_HOST = 'http://localhost:4567'

PREFIX = SERVICE_HOST + '/api/v1'

# V2 url support for differential logging
if hasattr(settings, "COMMENTS_SERVICE_V2_URL"):
    SERVICE_HOST_V2 = settings.COMMENTS_SERVICE_V2_URL
else:
    SERVICE_HOST_V2 = 'http://localhost:8000'

PREFIX_V2 = SERVICE_HOST_V2 + '/forum/forum_proxy/api/v1'
