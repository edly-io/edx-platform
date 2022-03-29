import logging

from django.contrib import auth
from django.core.cache import cache
from django.utils.deprecation import MiddlewareMixin

from openedx.features.clearesult_features.constants import (
    USER_SESSION_CACHE_KEY_SUFFIX,
    USER_SESSION_CACHE_TIMEOUT
)
from openedx.features.clearesult_features.models import ClearesultUserSession

logger = logging.getLogger(__name__)


class ClearesultSessionMiddleware(MiddlewareMixin):
    """
    Middleware to store session keys in cache and database.
    """

    def process_request(self, request):
        user = request.user
        if user.is_authenticated:
            incoming_session_key = request.session.session_key
            cache_key = self._get_cache_key(user.email)
            cached_user_sessions = cache.get(cache_key)

            if not cached_user_sessions:
                self._save_session_in_db(user, incoming_session_key)
                cached_user_sessions = [incoming_session_key]
                cache.set(cache_key, cached_user_sessions, USER_SESSION_CACHE_TIMEOUT)
            else:
                if incoming_session_key not in cached_user_sessions:
                    self._save_session_in_db(user, incoming_session_key)
                    cached_user_sessions.append(incoming_session_key)
                    cache.set(cache_key, cached_user_sessions, USER_SESSION_CACHE_TIMEOUT)

    def _get_cache_key(self, email):
        return email + USER_SESSION_CACHE_KEY_SUFFIX

    def _save_session_in_db(self, user, incoming_session_key):
        _, created = ClearesultUserSession.objects.get_or_create(user=user, session_key=incoming_session_key)
        if created:
            logger.info("New session for user ({}) with session key ({}) has been created.".format(
                        user.email, incoming_session_key))
