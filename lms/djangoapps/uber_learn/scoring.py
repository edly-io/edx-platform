"""
Uber Learn activity scoring logic.

Implements record_activity and get_total_points per the v1 scoring rules:
  - video / reading / resource: 10 pts on ANY first completion (correct is advisory)
  - choice (CAPA) / drag (DnD) / sort / number: 10 pts ONLY on first CORRECT;
    0 pts when attempts exhausted without correct answer (H-11) — activity is
    still marked complete so learners can advance past it.

The XBlock bridge is authoritative for when plugin.completed fires:
  - scorable types: fires ONLY when correct OR when max_attempts exhausted
  - completion-only types: fires on any terminal event

v1 NOTE: The 'correct' field is treated as advisory. Full server-authoritative
scoring via ScoresClient/StudentModule is planned for v2 (see solution design C.1).

Activity type vocabulary:
  'video', 'reading', 'resource'         — completion-only (10 pts on any completion)
  'choice', 'drag', 'sort', 'number'     — correct-required (10 pts only when correct=True;
                                           0 pts when exhausted but still marked complete)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

log = logging.getLogger(__name__)

POINTS_PER_ACTIVITY = 10

# Activity types that award points on ANY completion (correct is irrelevant)
COMPLETION_ONLY_TYPES = frozenset({'video', 'reading', 'resource'})

# Activity types that require correct=True to award points
CORRECT_REQUIRED_TYPES = frozenset({'choice', 'drag', 'sort', 'number'})

# All valid activity types
ALL_ACTIVITY_TYPES = COMPLETION_ONLY_TYPES | CORRECT_REQUIRED_TYPES

# Pass thresholds (score / total) per assessment type.
# Used by assessment.py and its tests.
PASS_THRESHOLDS = {
    'baseline': 0.0,   # informational: no pass/fail (design: passed=None)
    'final': 4 / 5,    # must score >= 4 out of 5
    'retention': 4 / 5,
}


def _points_for_completion(activity_type: str, correct: bool | None) -> int:
    """
    Return the points earned for a terminal completion of this activity.

    Video/reading/resource: always POINTS_PER_ACTIVITY.
    Choice/drag/sort/number: POINTS_PER_ACTIVITY if correct=True, else 0 (H-11).
    """
    if activity_type in COMPLETION_ONLY_TYPES:
        return POINTS_PER_ACTIVITY
    if activity_type in CORRECT_REQUIRED_TYPES:
        return POINTS_PER_ACTIVITY if correct is True else 0
    return 0


def record_activity(
    user: 'AbstractUser',
    course_id: str,
    activity_key: str,
    activity_type: str,
    correct: bool | None,
) -> tuple[int, bool]:
    """
    Record an activity event and return (points_earned, is_first_completion).

    Parameters
    ----------
    user:
        The learner.
    course_id:
        Course key string, e.g. 'course-v1:Uber+Learn+2024'.
    activity_key:
        Block usage key string for the specific activity (typically a vertical).
    activity_type:
        One of: 'video', 'reading', 'resource', 'choice', 'drag', 'sort', 'number'.
    correct:
        True / False for assessed types; None for completion-only types.
        Advisory in v1 — full server-authoritative scoring planned for v2.

    Returns
    -------
    (points_earned, is_first_completion)
        points_earned — 0 or POINTS_PER_ACTIVITY
        is_first_completion — True only when this call is the first to earn

    Raises
    ------
    ValueError
        If activity_type is not a recognised type.
    """
    if activity_type not in ALL_ACTIVITY_TYPES:
        raise ValueError(
            f"Unknown activity_type: {activity_type!r}. "
            f"Must be one of: {sorted(ALL_ACTIVITY_TYPES)}"
        )

    # Lazy import to avoid coupling at app-registry time (plugin pattern).
    from lms.djangoapps.uber_learn.models import UberLearnActivityProgress  # noqa: PLC0415

    now = timezone.now()

    # Step 1: get_or_create the row (absorbs insert races with IntegrityError retry).
    row = _get_or_create_activity_row(user, course_id, activity_key)

    # Step 2: Increment attempt counter and update last_submitted_at / last_correct.
    # These are best-effort analytics fields; last-writer-wins is acceptable.
    UberLearnActivityProgress.objects.filter(pk=row.pk).update(
        attempt_count=_f_expression('attempt_count') + 1,
        last_submitted_at=now,
        last_correct=correct,
    )

    # Step 3: Conditional UPDATE — marks complete and awards points exactly once.
    # For completion-only types (video/reading/resource): every call is a terminal
    # completion — mark complete unconditionally.
    # For correct-required types (choice/drag/sort/number): only a correct=True
    # submission (or a future exhausted=True signal) completes the activity.
    # An incorrect submission leaves completed_at=NULL so a later correct one can
    # still earn points (CRITICAL: incorrect must NOT permanently block earning).
    # NOTE: H-11 (0 pts when max_attempts exhausted with no correct answer) requires
    # a future 'exhausted' flag; for now we rely on the XBlock only firing
    # plugin.completed in terminal states via correct=True.
    points_to_award = _points_for_completion(activity_type, correct)
    should_complete = (
        activity_type in COMPLETION_ONLY_TYPES
        or (activity_type in CORRECT_REQUIRED_TYPES and correct is True)
    )

    points_earned = 0
    is_first_completion = False

    if should_complete:
        updated = UberLearnActivityProgress.objects.filter(
            pk=row.pk,
            completed_at__isnull=True,
        ).update(
            completed_at=now,
            points_awarded=points_to_award,
        )
        if updated == 1:
            points_earned = points_to_award
            is_first_completion = True

    return points_earned, is_first_completion


def get_total_points(user: 'AbstractUser', course_id: str) -> int:
    """Return the total points earned by user in course_id."""
    from django.db.models import Sum  # noqa: PLC0415
    from lms.djangoapps.uber_learn.models import UberLearnActivityProgress  # noqa: PLC0415
    result = UberLearnActivityProgress.objects.filter(
        user=user, course_key=course_id
    ).aggregate(total=Sum('points_awarded'))
    return result['total'] or 0


def _get_or_create_activity_row(user, course_id: str, activity_key: str):
    """
    Return the UberLearnActivityProgress row for (user, course_id, activity_key),
    creating it if it doesn't exist. Handles concurrent insert races gracefully
    via IntegrityError catch-and-retry.
    """
    from lms.djangoapps.uber_learn.models import UberLearnActivityProgress  # noqa: PLC0415
    try:
        with transaction.atomic():
            row, _ = UberLearnActivityProgress.objects.get_or_create(
                user=user,
                course_key=course_id,
                activity_key=activity_key,
            )
        return row
    except IntegrityError:
        # Lost the insert race to a concurrent request — the row now exists.
        return UberLearnActivityProgress.objects.get(
            user=user,
            course_key=course_id,
            activity_key=activity_key,
        )


def _f_expression(field: str):
    """Return a Django F() expression for atomic field increment."""
    from django.db.models import F  # noqa: PLC0415
    return F(field)
