# lint-amnesty, pylint: disable=cyclic-import, missing-module-docstring
from django.conf import settings

# FORUM V1 SERVICE HOST
if hasattr(settings, "COMMENTS_SERVICE_URL"):
    COMMENTS_SERVICE_SERVICE_HOST = settings.COMMENTS_SERVICE_URL
else:
    COMMENTS_SERVICE_SERVICE_HOST = "http://localhost:4567"

COMMENTS_SERVICE_PREFIX = COMMENTS_SERVICE_SERVICE_HOST + "/api/v1"


if hasattr(settings, "FORUM_V2_SERVICE_URL"):
    SERVICE_HOST = settings.FORUM_V2_SERVICE_URL
else:
    SERVICE_HOST = "http://localhost:8000"

PREFIX = SERVICE_HOST + "/forum/api/v2"
