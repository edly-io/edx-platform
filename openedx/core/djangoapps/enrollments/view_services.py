"""
Shared service layer for enrollment HTTP operations.

ADR 0031 (Merge Similar Endpoints) requires that closely related endpoints
that operate on the same resource domain consolidate their *business logic*
into a common service layer.  In this app three pairs of endpoints exist
side by side and share substantial logic:

* ``EnrollmentViewSet.create`` (canonical, ADR 0028) and
  ``EnrollmentListView.post`` (deprecated alias) — full create/update flow.
* ``EnrollmentViewSet.unenroll`` and ``UnenrollmentView.post`` — retirement
  unenroll-all-courses flow.
* ``EnrollmentViewSet.allowed`` and ``EnrollmentAllowedView`` — allowed-
  enrollment GET / POST / DELETE flow.

Without a shared service the deprecated alias and the canonical view drift
apart over time, defeating the point of having both.  This module hosts the
shared implementation that both sides call into.

Authorization model (ADR 0031)
------------------------------

Each operation is enforced in two layers:

1. The view declares a coarse permission class (``IsAuthenticated``,
   ``IsAdminUser``, ``CanRetireUser``, ``ApiKeyHeaderPermissionIsAuthenticated``)
   that filters out callers who have no business hitting the endpoint at all.
2. The service method enforces the *operation-specific* permission — e.g.
   only API-key callers or global staff may deactivate enrollments, downgrade
   modes, or force-enroll a user.  These checks live next to the business
   logic so the deprecated and canonical views cannot diverge on them.

This keeps the distinct authorization requirements of the legacy endpoints
intact while removing the duplicated boilerplate around them.
"""

import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import status
from rest_framework.response import Response

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.auth import user_has_role
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed, EnrollmentNotAllowed
from common.djangoapps.student.roles import CourseStaffRole, GlobalStaff
from openedx.core.djangoapps.course_groups.cohorts import CourseUserGroup, add_user_to_cohort, get_cohort_by_name
from openedx.core.djangoapps.embargo import api as embargo_api
from openedx.core.djangoapps.enrollments import api
from openedx.core.djangoapps.enrollments.errors import (
    CourseEnrollmentError,
    CourseEnrollmentExistsError,
    CourseModeNotFoundError,
    InvalidEnrollmentAttribute,
)
from openedx.core.djangoapps.user_api.models import UserRetirementStatus
from openedx.core.djangoapps.user_api.preferences.api import update_email_opt_in
from openedx.core.lib.exceptions import CourseNotFoundError
from openedx.core.lib.log_utils import audit_log
from openedx.features.enterprise_support.api import (
    ConsentApiServiceClient,
    EnterpriseApiException,
    EnterpriseApiServiceClient,
    enterprise_enabled,
)

log = logging.getLogger(__name__)

User = get_user_model()

REQUIRED_ATTRIBUTES = {
    "credit": ["credit:provider_id"],
}


class EnrollmentOperationsService:
    """
    Operation handlers shared between the canonical enrollment ViewSet and
    its deprecated APIView aliases.  See module docstring for the rationale
    and the two-layer authorization model required by ADR 0031.
    """

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------
    def list_enrollments_for_user(self, request_user, target_username, has_api_key):
        """
        Return the enrollments visible to ``request_user`` for the user
        named by ``target_username``.

        Per-operation permission (ADR 0031 layer 2):

        * If the requester is asking about themselves, global staff, or a
          server-to-server caller — the full enrollment list is returned.
        * Otherwise the list is filtered to courses the requester has the
          ``CourseStaffRole`` for (so a course team member can see whether
          a particular learner is in their course, but nothing more).

        Returns a list of ``CourseEnrollment`` instances.  Both the paginated
        canonical ``EnrollmentViewSet.list`` and the unpaginated deprecated
        ``EnrollmentListView.get`` call this helper.
        """
        enrollments = CourseEnrollment.objects.filter(
            user__username=target_username
        ).select_related("user", "course")
        if (
            target_username == request_user.username
            or GlobalStaff().has_user(request_user)
            or has_api_key
        ):
            return list(enrollments)
        return [
            enrollment for enrollment in enrollments
            if user_has_role(request_user, CourseStaffRole(enrollment.course_id))
        ]

    # ------------------------------------------------------------------
    # Create / update
    # ------------------------------------------------------------------
    def create_or_update_enrollment(self, request, has_api_key, course_id):
        """
        Handle the POST /enrollment/ create-or-update flow.

        Returns a DRF ``Response`` with the appropriate status code.  Mode-
        specific authorization (deactivation, mode-change, force-enroll) is
        enforced inside this method so the deprecated alias and the canonical
        view cannot drift on it.

        ``course_id`` is the validated ``CourseKey`` instance — callers are
        responsible for the up-front InvalidKeyError -> 400 mapping (because
        DRF view error responses are tightly coupled to the view's request
        parsing, and the deprecated and canonical wrappers handle that step
        identically).
        """
        # pylint: disable=too-many-statements,too-many-branches
        username = request.data.get("user")
        mode = request.data.get("mode")
        is_active = None  # populated below; referenced by the audit-log finally block
        user = None
        cohort_name = None

        # Per-operation authz layer 1: only admin/api-key callers may enroll
        # other users.  Anonymous and non-staff callers can only enroll
        # themselves.
        if (
            username
            and username != request.user.username
            and not has_api_key
            and not GlobalStaff().has_user(request.user)
        ):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not username:
            email = request.data.get("email")
            if email:
                if not has_api_key and not GlobalStaff().has_user(request.user):
                    return Response(status=status.HTTP_404_NOT_FOUND)
                try:
                    username = User.objects.get(email=email).username
                except ObjectDoesNotExist:
                    return Response(
                        status=status.HTTP_406_NOT_ACCEPTABLE,
                        data={"message": f"The user with the email address {email} does not exist."},
                    )
            else:
                username = request.user.username

        # Per-operation authz layer 2: non-default mode enrollments require
        # api-key or global-staff privileges.
        if (
            mode not in (CourseMode.AUDIT, CourseMode.HONOR, None)
            and not has_api_key
            and not GlobalStaff().has_user(request.user)
        ):
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={
                    "message": "User does not have permission to create enrollment with mode [{mode}].".format(
                        mode=mode,
                    ),
                },
            )

        try:
            user = User.objects.get(username=username)
        except ObjectDoesNotExist:
            return Response(
                status=status.HTTP_406_NOT_ACCEPTABLE,
                data={"message": f"The user {username} does not exist."},
            )

        embargo_response = embargo_api.get_embargo_response(request, course_id, user)
        if embargo_response:
            return embargo_response

        try:
            is_active = request.data.get("is_active")
            if is_active is not None and not isinstance(is_active, bool):
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={
                        "message": "'{value}' is an invalid enrollment activation status.".format(value=is_active),
                    },
                )

            explicit_linked_enterprise = request.data.get("linked_enterprise_customer")
            if explicit_linked_enterprise and has_api_key and enterprise_enabled():
                enterprise_api_client = EnterpriseApiServiceClient()
                consent_client = ConsentApiServiceClient()
                try:
                    enterprise_api_client.post_enterprise_course_enrollment(username, str(course_id))
                except EnterpriseApiException as error:
                    log.exception(
                        "An unexpected error occurred while creating the new EnterpriseCourseEnrollment "
                        "for user [%s] in course run [%s]",
                        username,
                        course_id,
                    )
                    raise CourseEnrollmentError(str(error))  # lint-amnesty, pylint: disable=raise-missing-from
                consent_client.provide_consent(
                    username=username,
                    course_id=str(course_id),
                    enterprise_customer_uuid=explicit_linked_enterprise,
                )

            enrollment_attributes = request.data.get("enrollment_attributes")
            force_enrollment = request.data.get("force_enrollment")
            if force_enrollment is not None and not isinstance(force_enrollment, bool):
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={
                        "message": "'{value}' is an invalid force enrollment status.".format(value=force_enrollment),
                    },
                )
            # Per-operation authz layer 2: force-enrollment is global-staff-only.
            force_enrollment = force_enrollment and GlobalStaff().has_user(request.user)

            enrollment = api.get_enrollment(username, str(course_id))
            mode_changed = enrollment and mode is not None and enrollment["mode"] != mode
            active_changed = enrollment and is_active is not None and enrollment["is_active"] != is_active
            missing_attrs = []
            if enrollment_attributes:
                actual_attrs = ["{namespace}:{name}".format(**attr) for attr in enrollment_attributes]
                missing_attrs = set(REQUIRED_ATTRIBUTES.get(mode, [])) - set(actual_attrs)

            # Per-operation authz layer 2: only api-key or global-staff
            # callers may switch an existing enrollment's mode or activation.
            if (GlobalStaff().has_user(request.user) or has_api_key) and (mode_changed or active_changed):
                if mode_changed and active_changed and not is_active:
                    msg = "Enrollment mode mismatch: active mode={}, requested mode={}. Won't deactivate.".format(
                        enrollment["mode"], mode,
                    )
                    log.warning(msg)
                    return Response(status=status.HTTP_400_BAD_REQUEST, data={"message": msg})

                if missing_attrs:
                    msg = "Missing enrollment attributes: requested mode={} required attributes={}".format(
                        mode, REQUIRED_ATTRIBUTES.get(mode),
                    )
                    log.warning(msg)
                    return Response(status=status.HTTP_400_BAD_REQUEST, data={"message": msg})

                response_data = api.update_enrollment(
                    username,
                    str(course_id),
                    mode=mode,
                    is_active=is_active,
                    enrollment_attributes=enrollment_attributes,
                    include_expired=has_api_key,
                )
            else:
                response_data = api.add_enrollment(
                    username,
                    str(course_id),
                    mode=mode,
                    is_active=is_active,
                    enrollment_attributes=enrollment_attributes,
                    enterprise_uuid=request.data.get("enterprise_uuid"),
                    force_enrollment=force_enrollment,
                    include_expired=force_enrollment,
                )

            cohort_name = request.data.get("cohort")
            if cohort_name is not None:
                cohort = get_cohort_by_name(course_id, cohort_name)
                try:
                    add_user_to_cohort(cohort, user)
                except ValueError:
                    log.exception("Cohort re-addition")

            email_opt_in = request.data.get("email_opt_in", None)
            if email_opt_in is not None:
                org = course_id.org
                update_email_opt_in(request.user, org, email_opt_in)

            log.info("The user [%s] has already been enrolled in course run [%s].", username, course_id)
            return Response(response_data)

        except InvalidEnrollmentAttribute as error:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": str(error), "localizedMessage": str(error)},
            )
        except EnrollmentNotAllowed as error:
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={"message": str(error), "localizedMessage": str(error)},
            )
        except CourseModeNotFoundError as error:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": (
                        "The [{mode}] course mode is expired or otherwise unavailable for course run [{course_id}]."
                    ).format(mode=mode, course_id=course_id),
                    "course_details": error.data,
                },
            )
        except CourseNotFoundError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": f"No course '{course_id}' found for enrollment"},
            )
        except CourseEnrollmentExistsError as error:
            log.warning("An enrollment already exists for user [%s] in course run [%s].", username, course_id)
            return Response(data=error.enrollment)
        except CourseEnrollmentError:
            log.exception(
                "An error occurred while creating the new course enrollment for user [%s] in course run [%s]",
                username,
                course_id,
            )
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    "message": (
                        "An error occurred while creating the new course enrollment for user "
                        "'{username}' in course '{course_id}'"
                    ).format(username=username, course_id=course_id),
                },
            )
        except CourseUserGroup.DoesNotExist:
            log.exception("Missing cohort [%s] in course run [%s]", cohort_name, course_id)
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"message": "An error occured while adding to cohort [%s]" % cohort_name},
            )
        finally:
            # Audit-log every API-key-driven enrollment change so the
            # ecommerce service's mode/activation requests can be traced.
            if has_api_key and user is not None:
                try:
                    current_enrollment_obj = CourseEnrollment.objects.get(
                        user__username=username, course_id=course_id,
                    )
                    actual_mode = current_enrollment_obj.mode
                    actual_activation = current_enrollment_obj.is_active
                except CourseEnrollment.DoesNotExist:
                    actual_mode = None
                    actual_activation = None
                audit_log(
                    "enrollment_change_requested",
                    course_id=str(course_id),
                    requested_mode=mode,
                    actual_mode=actual_mode,
                    requested_activation=is_active,
                    actual_activation=actual_activation,
                    user_id=user.id,
                )

    # ------------------------------------------------------------------
    # Unenroll (retirement pipeline)
    # ------------------------------------------------------------------
    def unenroll_user_for_retirement(self, username):
        """
        Handle the retirement-pipeline POST /unenroll/ flow.

        Returns a DRF ``Response``.  Caller is responsible for the coarse
        ``IsAuthenticated + CanRetireUser`` permission check (ADR 0031 layer
        1); this method handles the per-operation flow:

        * 404 if no retirement-status row exists for the username.
        * 204 if the user has no active enrollments.
        * 200 with the unenroll-result list otherwise.
        """
        if not username:
            return Response("Username not specified.", status=status.HTTP_404_NOT_FOUND)
        try:
            UserRetirementStatus.get_retirement_for_retirement_action(username)
        except UserRetirementStatus.DoesNotExist:
            return Response(
                "No retirement request status for username.",
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            active_enrollments = CourseEnrollment.objects.filter(
                user__username=username, is_active=True,
            )
            if not active_enrollments.exists():
                return Response(status=status.HTTP_204_NO_CONTENT)
            return Response(api.unenroll_user_from_all_courses(username))
        except Exception as exc:  # pylint: disable=broad-except
            return Response(str(exc), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ------------------------------------------------------------------
    # Allowed enrollments
    # ------------------------------------------------------------------
    def list_allowed_for_email(self, email):
        """Return the ``CourseEnrollmentAllowed`` rows for ``email``."""
        return CourseEnrollmentAllowed.objects.filter(email=email)

    def create_allowed_enrollment(self, serializer):
        """
        Persist the allowed-enrollment described by ``serializer``.

        Raises ``IntegrityError`` if a row already exists for the
        (email, course_id) pair so the calling view can translate it into
        a 409 ``Conflict``.
        """
        return serializer.save()

    def delete_allowed_enrollment(self, email, course_id):
        """
        Delete the allowed-enrollment row identified by (email, course_id).

        Raises ``ObjectDoesNotExist`` if no such row exists so the calling
        view can translate it into a 404.
        """
        CourseEnrollmentAllowed.objects.get(email=email, course_id=course_id).delete()
