"""
v3-scoped mixins for the contentstore REST API.

Currently provides :class:`StandardizedErrorMixin`, which opts a single
view/viewset into the ADR 0029 error envelope without changing the
project-wide DRF ``EXCEPTION_HANDLER`` setting.
"""
from openedx.core.lib.api.exceptions import standardized_error_exception_handler


class StandardizedErrorMixin:
    """
    Opt-in mixin that routes DRF exceptions on this view through the ADR 0029
    standardized error-response handler (see
    ``openedx.core.lib.api.exceptions.standardized_error_exception_handler``).

    DRF's :class:`rest_framework.views.APIView` calls ``self.get_exception_handler``
    inside ``handle_exception``; overriding that method here lets v3 endpoints
    return the standardized envelope while v0/v1/v2 endpoints continue to use
    whichever handler the project-wide ``EXCEPTION_HANDLER`` setting points at.

    Usage::

        class MyViewSet(StandardizedErrorMixin, viewsets.ViewSet):
            ...
    """

    def get_exception_handler(self):
        return standardized_error_exception_handler
