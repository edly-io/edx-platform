"""
Uber Learn badge evaluation.

Badge rules (C.6):
  - 'applied':  awarded when user passes the final assessment for the first time
  - 'thorough': awarded when user has 'applied' badge AND all activities complete
                (activities_total must be non-None and > 0;
                activities_completed >= activities_total)
  - 'retained': awarded when user passes the retention assessment for the first time

All badges are sticky: UniqueConstraint prevents duplicates — get_or_create is used
so concurrent requests are safe without raising.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

log = logging.getLogger(__name__)


def evaluate_and_award_badges(
    user: 'AbstractUser',
    course_id: str,
    activities_total: int | None,
) -> list[str]:
    """
    Evaluate and award badges after a scoring event.

    Returns a list of badge_type strings for badges newly awarded in this call.
    Already-held badges are silently skipped (sticky semantics).

    Parameters
    ----------
    user:
        The learner.
    course_id:
        Course key string.
    activities_total:
        Total number of activities in the course (from course manifest / course
        blocks walk).  May be None when the manifest is unavailable — in that
        case the 'thorough' badge is never awarded.
    """
    # Lazy imports to avoid app-registry coupling at module load time.
    from lms.djangoapps.uber_learn.models import (  # noqa: PLC0415
        UberLearnActivityProgress,
        UberLearnAssessmentAttempt,
        UberLearnBadgeAward,
    )

    newly_awarded: list[str] = []

    # --- 'applied': first time passing the final assessment -----------------
    has_applied = UberLearnBadgeAward.objects.filter(
        user=user, course_key=course_id, badge_type='applied'
    ).exists()

    if not has_applied:
        final_passed = UberLearnAssessmentAttempt.objects.filter(
            user=user,
            course_key=course_id,
            assessment_type='final',
            passed=True,
        ).exists()
        if final_passed:
            _, created = UberLearnBadgeAward.objects.get_or_create(
                user=user, course_key=course_id, badge_type='applied'
            )
            if created:
                newly_awarded.append('applied')
                log.info(
                    "Awarded 'applied' badge: user=%s course=%s",
                    user.id, course_id,
                )
                has_applied = True  # used in thorough check below

    # --- 'thorough': applied badge + all activities complete ----------------
    if has_applied and activities_total and activities_total > 0:
        has_thorough = UberLearnBadgeAward.objects.filter(
            user=user, course_key=course_id, badge_type='thorough'
        ).exists()
        if not has_thorough:
            completed_count = UberLearnActivityProgress.objects.filter(
                user=user,
                course_key=course_id,
                completed_at__isnull=False,
            ).count()
            if completed_count >= activities_total:
                _, created = UberLearnBadgeAward.objects.get_or_create(
                    user=user, course_key=course_id, badge_type='thorough'
                )
                if created:
                    newly_awarded.append('thorough')
                    log.info(
                        "Awarded 'thorough' badge: user=%s course=%s "
                        "(completed=%d, total=%d)",
                        user.id, course_id, completed_count, activities_total,
                    )

    # --- 'retained': first time passing the retention assessment ------------
    has_retained = UberLearnBadgeAward.objects.filter(
        user=user, course_key=course_id, badge_type='retained'
    ).exists()

    if not has_retained:
        retention_passed = UberLearnAssessmentAttempt.objects.filter(
            user=user,
            course_key=course_id,
            assessment_type='retention',
            passed=True,
        ).exists()
        if retention_passed:
            _, created = UberLearnBadgeAward.objects.get_or_create(
                user=user, course_key=course_id, badge_type='retained'
            )
            if created:
                newly_awarded.append('retained')
                log.info(
                    "Awarded 'retained' badge: user=%s course=%s",
                    user.id, course_id,
                )

    return newly_awarded
