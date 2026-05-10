"""
ADR 0029 – Standardized error-response exception handler and helpers.
"""
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response


class Conflict(APIException):
    """HTTP 409 Conflict — ADR 0029."""
    status_code = 409
    default_detail = "A conflict occurred."
    default_code = "conflict"


def standardized_error_exception_handler(exc, context):
    """ADR 0029 – platform-level DRF exception handler."""
    from openedx.core.lib.request_utils import ignored_error_exception_handler
    response = ignored_error_exception_handler(exc, context)
    if response is None:
        return Response(
            {"type": "https://docs.openedx.org/errors/internal",
             "title": "Internal Server Error", "status": 500,
             "detail": "An unexpected error occurred. Please try again later."},
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


def _error_type(exc):
    """Map a DRF exception to the ADR 0029 ``type`` slug used in the error URL."""
    from rest_framework.exceptions import (
        AuthenticationFailed,
        NotAuthenticated,
        NotFound,
        PermissionDenied,
        Throttled,
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
    """Return the human-readable ADR 0029 ``title`` for a DRF exception."""
    from rest_framework.exceptions import (
        AuthenticationFailed,
        NotAuthenticated,
        NotFound,
        PermissionDenied,
        Throttled,
    )
    return {
        NotAuthenticated: "Authentication Required",
        AuthenticationFailed: "Authentication Failed",
        PermissionDenied: "Permission Denied",
        NotFound: "Not Found",
        ValidationError: "Validation Error",
        Throttled: "Too Many Requests",
        Conflict: "Conflict",
    }.get(type(exc), "Internal Server Error")


def _flatten_detail(data):
    """Reduce DRF's nested ``detail`` payload to a single user-facing string."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    if isinstance(data, list) and data:
        return str(data[0])
    return str(data)


def _normalize_validation_errors(detail):
    """Coerce DRF validation-error payloads into the ADR 0029 ``errors`` dict shape."""
    if isinstance(detail, dict):
        return {f: [str(e) for e in (errs if isinstance(errs, list) else [errs])] for f, errs in detail.items()}
    if isinstance(detail, list):
        return {"non_field_errors": [str(e) for e in detail]}
    return {"non_field_errors": [str(detail)]}
