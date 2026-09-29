"""
Views for the Uber Learn Progress API.

Base path: /api/uber_learn/v1/  (registered through PluginURLs in apps.py)

Endpoints:
  GET  /api/uber_learn/v1/progress/<course_key>
  POST /api/uber_learn/v1/progress/<course_key>/activity
  POST /api/uber_learn/v1/progress/<course_key>/assessment

Authentication: JwtAuthentication + SessionAuthenticationAllowInactiveUser
Authorization:  IsAuthenticated + enrolled in course
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import (
    SessionAuthenticationAllowInactiveUser,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.uber_learn.assessment import (
    AlreadyPassedError,
    CooldownActiveError,
    ConcurrentSubmissionError,
    check_assessment_eligibility,
    record_assessment,
)
from lms.djangoapps.uber_learn.course_structure import (
    AssessmentIncompleteError,
    AssessmentNotConfiguredError,
    read_assessment_config,
    score_assessment,
)
from lms.djangoapps.uber_learn.models import (
    UberLearnActivityProgress,
    UberLearnAssessmentAttempt,
    UberLearnBadgeAward,
)
from lms.djangoapps.uber_learn.badges import evaluate_and_award_badges
from lms.djangoapps.uber_learn.scoring import (
    POINTS_PER_ACTIVITY,
    record_activity,
)
from lms.djangoapps.uber_learn.serializers import (
    ActivityRequestSerializer,
    AssessmentRequestSerializer,
)

log = logging.getLogger(__name__)


def _get_activities_total(user, course_id: str) -> int | None:
    """
    Return the number of vertical blocks (activities) in the course.

    Uses the course block structure walk, matching the pattern in the edx-platform
    Progress tab.  Wrapped in a broad except so any modulestore / block-structure
    error degrades gracefully to None instead of crashing the progress response.
    """
    try:
        from lms.djangoapps.course_blocks.api import get_course_blocks  # noqa: PLC0415
        from opaque_keys.edx.keys import CourseKey  # noqa: PLC0415
        from xmodule.modulestore.django import modulestore  # noqa: PLC0415

        course_key_obj = CourseKey.from_string(course_id)
        store = modulestore()
        course = store.get_course(course_key_obj, depth=0)
        if course is None:
            return None
        block_structure = get_course_blocks(user, course.location)
        count = sum(
            1
            for block_key in block_structure
            if block_structure.get_xblock_field(block_key, 'category') == 'vertical'
        )
        return count if count > 0 else None
    except Exception:  # noqa: BLE001
        log.warning(
            "Failed to get course blocks for activities_total: course=%s", course_id
        )
        return None


class _BaseUberLearnView(APIView):
    """Shared authentication, permission, and enrollment check for all Uber Learn views."""

    authentication_classes = (
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)

    def _check_enrollment(self, user, course_id: str) -> bool:
        """Return True if the user is actively enrolled in the course."""
        from common.djangoapps.student.models import CourseEnrollment  # noqa: PLC0415
        try:
            from opaque_keys.edx.keys import CourseKey  # noqa: PLC0415
            course_key = CourseKey.from_string(course_id)
            return CourseEnrollment.is_enrolled(user, course_key)
        except Exception:  # noqa: BLE001
            return False


def _build_progress_response(user, course_id: str) -> dict:
    """
    Aggregate all progress data for a user in a course.

    `activities.total` and `points.possible` are derived from the course block
    structure (get_course_blocks) when available; they degrade to None if the
    modulestore is unreachable or the course is not found.
    """
    now = timezone.now()
    activities_total = _get_activities_total(user, course_id)
    activities = list(UberLearnActivityProgress.objects.filter(
        user=user, course_key=course_id
    ))
    completed_rows = [a for a in activities if a.completed_at is not None]
    points_earned = sum(a.points_awarded for a in completed_rows)

    # Badges
    badge_awards = list(UberLearnBadgeAward.objects.filter(
        user=user, course_key=course_id
    ))
    badges = [
        {'badge_type': b.badge_type, 'awarded_at': b.awarded_at.isoformat()}
        for b in badge_awards
    ]

    # Retention unlock: first final pass + 30 days
    final_pass = UberLearnAssessmentAttempt.objects.filter(
        user=user, course_key=course_id, assessment_type='final', passed=True
    ).order_by('submitted_at').first()
    retention_unlocked_at = None
    if final_pass:
        retention_unlocked_at = final_pass.submitted_at + timedelta(days=30)

    # Streak: consecutive UTC days of first completions (computed in Python, avoiding MySQL TZ deps)
    completion_dates = sorted(
        {a.completed_at.date() for a in completed_rows if a.completed_at},
        reverse=True,
    )
    today = now.date()
    yesterday = today - timedelta(days=1)
    active_today = bool(completion_dates) and completion_dates[0] == today

    if not completion_dates or completion_dates[0] < yesterday:
        current_streak = 0
    else:
        current_streak = 1
        for i in range(1, len(completion_dates)):
            if (completion_dates[i - 1] - completion_dates[i]).days == 1:
                current_streak += 1
            else:
                break

    longest_streak = 1
    if completion_dates:
        run = 1
        for i in range(1, len(completion_dates)):
            if (completion_dates[i - 1] - completion_dates[i]).days == 1:
                run += 1
                longest_streak = max(longest_streak, run)
            else:
                run = 1

    course_complete = any(b.badge_type == 'thorough' for b in badge_awards)
    course_completed_at = next(
        (b.awarded_at.isoformat() for b in badge_awards if b.badge_type == 'thorough'), None
    )

    # Build assessment states
    assessments = {
        a_type: _build_assessment_state(user, course_id, a_type, retention_unlocked_at, now)
        for a_type in ('baseline', 'final', 'retention')
    }

    return {
        'course_id': course_id,
        'server_time': now.isoformat(),
        'points': {
            'earned': points_earned,
            'possible': (
                activities_total * POINTS_PER_ACTIVITY
                if activities_total is not None
                else None
            ),
            'per_activity': POINTS_PER_ACTIVITY,
        },
        'activities': {
            'completed': len(completed_rows),
            'total': activities_total,
            'items': [],    # TODO: full item list requires course manifest walk
        },
        'assessments': assessments,
        'retention_unlocked_at': retention_unlocked_at.isoformat() if retention_unlocked_at else None,
        'streak': {
            'current_days': current_streak,
            'longest_days': longest_streak,
            'active_today': active_today,
            'last_active_date': completion_dates[0].isoformat() if completion_dates else None,
        },
        'badges': badges,
        'course_complete': course_complete,
        'course_completed_at': course_completed_at,
    }


def _build_assessment_state(user, course_id: str, a_type: str, retention_unlocked_at, now) -> dict:
    """Build the AssessmentState sub-object for one assessment type."""
    # Read assessment block key from course config (graceful: None on any error).
    _config = read_assessment_config(course_id)
    assessment_usage_key = _config.get(a_type)
    configured = assessment_usage_key is not None

    attempts = list(UberLearnAssessmentAttempt.objects.filter(
        user=user, course_key=course_id, assessment_type=a_type
    ).order_by('submitted_at'))

    attempt_count = len(attempts)
    first_passed = next((a for a in attempts if a.passed is True), None)

    eligibility = check_assessment_eligibility(user, course_id, a_type)

    # Determine block reason
    blocked_reason = None
    retry_after = 0
    if not eligibility.allowed:
        blocked_reason = eligibility.reason or 'cooldown'
        retry_after = eligibility.wait_seconds

    latest = attempts[-1] if attempts else None
    latest_dict = None
    if latest:
        latest_dict = {
            'attempt_number': latest.attempt_number,
            'correct_count': latest.correct_count,
            'total_count': latest.total_count,
            'passed': latest.passed,
            'submitted_at': latest.submitted_at.isoformat(),
            'question_results': latest.question_results or [],
        }

    next_available_at = None
    if not eligibility.allowed and latest:
        next_available_at = (
            latest.submitted_at + timedelta(seconds=retry_after)
        ).isoformat()

    unlocked = True
    if a_type == 'retention':
        unlocked = (
            retention_unlocked_at is not None
            and now >= retention_unlocked_at
        )
        if not unlocked and blocked_reason is None:
            if retention_unlocked_at is None:
                blocked_reason = 'final_not_passed'
            else:
                blocked_reason = 'retention_locked'
                retry_after = max(0, int((retention_unlocked_at - now).total_seconds()) + 1)

    return {
        'configured': configured,
        'usage_key': assessment_usage_key,
        'sequence_key': assessment_usage_key,   # may be a sequential; same key serves as sequence_key
        'first_unit_key': None,                 # TODO: walk block structure to find first vertical
        'question_count': latest.total_count if latest else None,
        'attempt_count': attempt_count,
        'passed': first_passed.passed if first_passed else (None if a_type == 'baseline' else False),
        'first_passed_at': first_passed.submitted_at.isoformat() if first_passed else None,
        'can_attempt': eligibility.allowed and unlocked,
        'blocked_reason': blocked_reason,
        'retry_after_seconds': retry_after,
        'next_attempt_available_at': next_available_at,
        'unlocked': unlocked,
        'unlocks_at': retention_unlocked_at.isoformat() if (
            a_type == 'retention' and retention_unlocked_at
        ) else None,
        'latest_attempt': latest_dict,
    }


def score_and_record_assessment(user, course_id: str, a_type: str, idem_key_str: str):
    """
    Orchestrate assessment scoring and persistence.

    Reads real grades from StudentModule (design doc E.2) and persists the attempt.

    Returns (attempt, passed, is_replay).
    Raises AssessmentNotConfiguredError, AssessmentIncompleteError,
           AlreadyPassedError, CooldownActiveError, ConcurrentSubmissionError,
           or ValueError.
    """
    idem_key = uuid.UUID(idem_key_str)

    # Idempotency replay check runs before eligibility and scoring (design doc G.2).
    existing = UberLearnAssessmentAttempt.objects.filter(
        user=user, idempotency_key=idem_key
    ).first()
    if existing:
        if existing.course_key != course_id or existing.assessment_type != a_type:
            raise ValueError('idempotency_key_conflict')
        return existing, existing.passed, True

    # Server-authoritative scoring: read grades from StudentModule (design doc E.2).
    # Raises AssessmentNotConfiguredError or AssessmentIncompleteError — let them
    # propagate to AssessmentView.post() for the correct HTTP status.
    scored = score_assessment(user=user, course_id=course_id, a_type=a_type)

    attempt, passed = record_assessment(
        user=user,
        course_id=course_id,
        assessment_type=a_type,
        score=scored['correct'],
        total=scored['total'],
        idempotency_key=idem_key,
        assessment_key=scored['assessment_key'],
        question_results=scored['question_results'],
    )
    return attempt, passed, False


class ProgressView(_BaseUberLearnView):
    """
    GET /api/uber_learn/v1/progress/<course_key>

    Returns aggregated progress data for an enrolled user.
    """

    def get(self, request, course_key):
        user = request.user
        if not self._check_enrollment(user, course_key):
            return Response(
                {'error_code': 'not_enrolled', 'developer_message': 'User is not enrolled.'},
                status=403,
            )
        data = _build_progress_response(user, course_key)
        return Response(data)


class ActivityView(_BaseUberLearnView):
    """
    POST /api/uber_learn/v1/progress/<course_key>/activity

    Records a single activity submission.

    Request body:
      activity_key  (str)       — vertical usage key
      correct       (bool|null) — advisory; server derives correctness from StudentModule (v2)

    Response 200:
      activity_key, is_scorable, server_correct, completed, newly_completed,
      points_awarded_now, points_awarded_total, completed_at, attempt_count,
      totals, streak, badges_awarded_now, course_complete, server_time
    """

    def post(self, request, course_key):
        user = request.user
        if not self._check_enrollment(user, course_key):
            return Response(
                {'error_code': 'not_enrolled', 'developer_message': 'User is not enrolled.'},
                status=403,
            )

        serializer = ActivityRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'error_code': 'invalid_request',
                    'developer_message': 'Request body validation failed.',
                    'field_errors': serializer.errors,
                },
                status=400,
            )

        data = serializer.validated_data
        activity_key = data['activity_key']
        client_correct = data.get('correct')

        try:
            points_now, newly_completed = record_activity(
                user=user,
                course_id=course_key,
                activity_key=activity_key,
                activity_type='video',   # v1: type derived from key in v2 via course manifest
                correct=client_correct,
            )
        except ValueError as exc:
            return Response(
                {'error_code': 'invalid_activity_key', 'developer_message': str(exc)},
                status=400,
            )
        except Exception:
            log.exception(
                "Error recording activity for user=%s course=%s key=%s",
                user.id, course_key, activity_key,
            )
            return Response(
                {'error_code': 'internal_error', 'developer_message': 'Activity recording failed.'},
                status=500,
            )

        activities_total = _get_activities_total(user, course_key)
        badges_now = evaluate_and_award_badges(user, course_key, activities_total)

        total_points = UberLearnActivityProgress.objects.filter(
            user=user, course_key=course_key
        ).aggregate(total=Sum('points_awarded'))['total'] or 0

        total_completed = UberLearnActivityProgress.objects.filter(
            user=user, course_key=course_key, completed_at__isnull=False
        ).count()

        row = UberLearnActivityProgress.objects.filter(
            user=user, course_key=course_key, activity_key=activity_key
        ).first()

        course_complete = UberLearnBadgeAward.objects.filter(
            user=user, course_key=course_key, badge_type='thorough'
        ).exists()

        now = timezone.now()
        return Response({
            'activity_key': activity_key,
            'is_scorable': True,    # TODO: from course manifest in v2
            'server_correct': client_correct,   # advisory until v2 StudentModule integration
            'completed': row.completed_at is not None if row else newly_completed,
            'newly_completed': newly_completed,
            'points_awarded_now': points_now,
            'points_awarded_total': (row.points_awarded if row else 0),
            'completed_at': row.completed_at.isoformat() if row and row.completed_at else None,
            'attempt_count': row.attempt_count if row else 1,
            'totals': {
                'points_earned': total_points,
                'points_possible': (
                    activities_total * POINTS_PER_ACTIVITY
                    if activities_total is not None
                    else None
                ),
                'activities_completed': total_completed,
                'activities_total': activities_total,
            },
            'streak': None,             # TODO: compute from activity rows in v2
            'badges_awarded_now': [
                {'badge_type': bt} for bt in badges_now
            ],
            'course_complete': course_complete,
            'server_time': now.isoformat(),
        })


class AssessmentView(_BaseUberLearnView):
    """
    POST /api/uber_learn/v1/progress/<course_key>/assessment

    Records an assessment attempt. The server is authoritative for scoring.

    Request body:
      assessment_type  (str)  — baseline | final | retention
      idempotency_key  (str)  — UUID v4; reused by client on retries

    Response 201 (new attempt) or 200 (replay):
      assessment_type, attempt, replayed, state, retention_unlocked_at,
      badges_awarded_now, course_complete, server_time
    """

    def post(self, request, course_key):
        user = request.user
        if not self._check_enrollment(user, course_key):
            return Response(
                {'error_code': 'not_enrolled', 'developer_message': 'User is not enrolled.'},
                status=403,
            )

        serializer = AssessmentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'error_code': 'invalid_request',
                    'developer_message': 'Request body validation failed.',
                    'field_errors': serializer.errors,
                },
                status=400,
            )

        data = serializer.validated_data
        a_type = data['assessment_type']
        # DRF UUIDField returns a UUID object; convert to str so score_and_record_assessment
        # can call uuid.UUID() on it without raising AttributeError.
        idem_key_str = str(data['idempotency_key'])

        try:
            attempt, passed, is_replay = score_and_record_assessment(
                user=user,
                course_id=course_key,
                a_type=a_type,
                idem_key_str=idem_key_str,
            )
        except AssessmentNotConfiguredError as exc:
            return Response(
                {
                    'error_code': 'assessment_not_configured',
                    'developer_message': str(exc),
                },
                status=404,
            )
        except AssessmentIncompleteError as exc:
            return Response(
                {
                    'error_code': 'assessment_incomplete',
                    'developer_message': 'One or more questions have no fresh graded submission.',
                    'missing_question_keys': exc.missing_keys,
                },
                status=409,
            )
        except AlreadyPassedError:
            return Response(
                {
                    'error_code': 'already_passed',
                    'developer_message': 'Assessment already passed; no retakes allowed.',
                },
                status=409,
            )
        except CooldownActiveError as exc:
            return Response(
                {
                    'error_code': 'cooldown_active',
                    'developer_message': 'Assessment cooldown is active.',
                    'retry_after_seconds': exc.wait_seconds,
                },
                status=429,
                headers={'Retry-After': str(exc.wait_seconds)},
            )
        except ConcurrentSubmissionError:
            return Response(
                {
                    'error_code': 'concurrent_submission',
                    'developer_message': 'Concurrent submission — GET progress and retry.',
                },
                status=409,
            )
        except ValueError as exc:
            if 'idempotency_key_conflict' in str(exc):
                return Response(
                    {'error_code': 'idempotency_key_conflict',
                     'developer_message': 'This idempotency_key was used with a different assessment.'},
                    status=409,
                )
            return Response(
                {'error_code': 'invalid_request', 'developer_message': str(exc)},
                status=400,
            )
        except Exception:
            log.exception(
                "Error recording assessment for user=%s course=%s type=%s",
                user.id, course_key, a_type,
            )
            return Response(
                {'error_code': 'internal_error', 'developer_message': 'Assessment recording failed.'},
                status=500,
            )

        # Evaluate badges after a successful assessment record.
        activities_total = _get_activities_total(user, course_key)
        badges_now = evaluate_and_award_badges(user, course_key, activities_total)

        # Retention unlock: first final-pass timestamp + 30 days.
        final_pass = UberLearnAssessmentAttempt.objects.filter(
            user=user, course_key=course_key, assessment_type='final', passed=True
        ).order_by('submitted_at').first()
        retention_unlocked_at = None
        if final_pass:
            retention_unlocked_at = (
                final_pass.submitted_at + timedelta(days=30)
            ).isoformat()

        course_complete = UberLearnBadgeAward.objects.filter(
            user=user, course_key=course_key, badge_type='thorough'
        ).exists()

        now = timezone.now()
        attempt_dict = {
            'attempt_number': attempt.attempt_number,
            'correct_count': attempt.correct_count,
            'total_count': attempt.total_count,
            'passed': attempt.passed,
            'submitted_at': attempt.submitted_at.isoformat(),
            'question_results': attempt.question_results or [],
        }

        status_code = 200 if is_replay else 201
        return Response(
            {
                'assessment_type': a_type,
                'attempt': attempt_dict,
                'replayed': is_replay,
                'state': None,              # TODO: _build_assessment_state in follow-up
                'retention_unlocked_at': retention_unlocked_at,
                'badges_awarded_now': [
                    {'badge_type': bt} for bt in badges_now
                ],
                'course_complete': course_complete,
                'server_time': now.isoformat(),
            },
            status=status_code,
        )
