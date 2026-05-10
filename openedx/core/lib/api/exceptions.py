"""
ADR 0029 – Standardized error-response exception handler and helpers.

Installs a platform-level DRF ``EXCEPTION_HANDLER`` that converts every
API exception into a single, consistent JSON envelope::

    {
      "type":         "https://docs.openedx.org/errors/<slug>",
      "title":        "Validation Error",
      "status":       400,
      "detail":       "The request body failed validation.",
      "instance":     "/api/enrollment/v1/enrollment/",
      "user_message": "...",   # optional – present only when set on exc
      "errors":       {...}    # optional – present only for ValidationError
    }

The handler chains through the existing ``ignored_error_exception_handler``
so that error logging / monitoring added by that handler is preserved.
"""

from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response


# ---------------------------------------------------------------------------
# Public exception classes
# ---------------------------------------------------------------------------

class Conflict(APIException):
    """HTTP 409 Conflict — ADR 0029."""

    status_code = 409
    default_detail = "A conflict occurred."
    default_code = "conflict"


# ---------------------------------------------------------------------------
# Central handler
# ---------------------------------------------------------------------------

def standardized_error_exception_handler(exc, context):
    """
    ADR 0029 – platform-level DRF exception handler.

    Chains through ``ignored_error_exception_handler`` so that its error
    logging / monitoring is preserved, then reformats the response to the
    ADR 0029 envelope shape.

    Returns a generic 500 body for unhandled exceptions so that stack
    traces are never leaked to callers.
    """
    # Chain through the existing handler to preserve monitoring side-effects.
    from openedx.core.lib.request_utils import ignored_error_exception_handler
    response = ignored_error_exception_handler(exc, context)

    if response is None:
        # Unhandled exception (e.g. IntegrityError, unexpected 500).
        # Always return a generic body — never expose stack traces.
        return Response(
            {
                "type": "https://docs.openedx.org/errors/internal",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "An unexpected error occurred. Please try again later.",
            },
            status=500,
        )

    request = context.get("request")
    body = {
        "type": f"https://docs.openedx.org/errors/{_error_type(exc)}",
        "title": _error_title(exc),
        "status": response.status_code,
        "detail": _flatten_detail(response.data),
    }
    if request:
        body["instance"] = request.path
    if hasattr(exc, "user_message") and exc.user_message:
        body["user_message"] = exc.user_message
    if isinstance(exc, ValidationError) and hasattr(exc, "detail"):
        body["errors"] = _normalize_validation_errors(exc.detail)

    response.data = body
    response["Content-Type"] = "application/json"
    return response


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _error_type(exc):
    """Map a DRF exception to an ADR 0029 error-type slug."""
    from rest_framework.exceptions import (
        AuthenticationFailed, NotAuthenticated, NotFound, PermissionDenied,
        Throttled, ValidationError,
    )
    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        return "authn"
    if isinstance(exc, PermissionDenied):
        return "authz"
    if isinstance(exc, NotFound):
        return "not-found"
    if isinstance(exc, ValidationError):
        return "validation"
    if isinstance(exc, Throttled):
        return "rate-limited"
    if isinstance(exc, Conflict):
        return "conflict"
    return "internal"


def _error_title(exc):
    """Return a short, developer-facing title for the exception class."""
    from rest_framework.exceptions import (
        AuthenticationFailed, NotAuthenticated, NotFound, PermissionDenied,
        Throttled, ValidationError,
    )
    _TITLES = {
        NotAuthenticated: "Authentication Required",
        AuthenticationFailed: "Authentication Failed",
        PermissionDenied: "Permission Denied",
        NotFound: "Not Found",
        ValidationError: "Validation Error",
        Throttled: "Too Many Requests",
        Conflict: "Conflict",
    }
    return _TITLES.get(type(exc), "Internal Server Error")


def _flatten_detail(data):
    """Extract a single string from DRF's response.data for the ``detail`` field."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    if isinstance(data, list) and data:
        return str(data[0])
    return str(data)


def _normalize_validation_errors(detail):
    """Normalize DRF ValidationError detail into ``{field: [msg, ...]}`` form."""
    if isinstance(detail, dict):
        return {
            field: [str(e) for e in (errs if isinstance(errs, list) else [errs])]
            for field, errs in detail.items()
        }
    if isinstance(detail, list):
        return {"non_field_errors": [str(e) for e in detail]}
    return {"non_field_errors": [str(detail)]}
