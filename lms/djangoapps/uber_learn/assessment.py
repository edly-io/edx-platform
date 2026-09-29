"""
Uber Learn assessment retry / cooldown logic.

Business rules:
  - baseline:          unlimited attempts, no cooldown, passed=None (informational only)
  - final / retention: max 3 attempts per 60s window → COOLDOWN (NOT permanent lockout)
                       after 60s, user may attempt again (counted from the last attempt)
  - Server is authoritative for pass/fail — client cannot submit a 'passed' field
  - Score > total is always invalid (ValueError)
  - Idempotency: client sends an idempotency_key UUID; retries replay the stored result
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from lms.djangoapps.uber_learn.scoring import PASS_THRESHOLDS

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

log = logging.getLogger(__name__)

COOLDOWN_SECONDS = 60
MAX_ATTEMPTS_PER_WINDOW = 3


@dataclass(frozen=True)
class EligibilityResult:
    """Result of checking whether a user may attempt an assessment."""

    allowed: bool
    wait_seconds: int        # 0 if allowed or already_passed
    next_attempt_number: int
    reason: str = ''         # 'cooldown' | 'already_passed' | '' when allowed


def check_assessment_eligibility(
    user: 'AbstractUser',
    course_id: str,
    assessment_type: str,
) -> EligibilityResult:
    """
    Return eligibility for the next assessment attempt.

    For 'baseline': always allowed, attempt count unlimited.
    For 'final' / 'retention':
      - already_passed: terminal state, no retakes allowed (H-2).
      - cooldown: blocked when a full batch of MAX_ATTEMPTS_PER_WINDOW has been
        exhausted and COOLDOWN_SECONDS have not elapsed since the last attempt.
        After the cooldown, a fresh batch of MAX_ATTEMPTS_PER_WINDOW is granted (H-1).
    """
    from lms.djangoapps.uber_learn.models import UberLearnAssessmentAttempt  # noqa: PLC0415

    attempts_qs = UberLearnAssessmentAttempt.objects.filter(
        user=user,
        course_key=course_id,
        assessment_type=assessment_type,
    ).order_by('submitted_at')

    count = attempts_qs.count()
    next_attempt_number = count + 1

    if assessment_type == 'baseline':
        return EligibilityResult(
            allowed=True,
            wait_seconds=0,
            next_attempt_number=next_attempt_number,
        )

    # H-2: no retakes after passing final/retention (terminal state)
    if attempts_qs.filter(passed=True).exists():
        return EligibilityResult(
            allowed=False,
            wait_seconds=0,
            next_attempt_number=next_attempt_number,
            reason='already_passed',
        )

    # Batch model: 3 free attempts per batch, then 60s cooldown before the next batch.
    # Cooldown gate fires only at exact batch boundaries (count divisible by batch size).
    if count > 0 and count % MAX_ATTEMPTS_PER_WINDOW == 0:
        last_attempt = attempts_qs.last()
        now = timezone.now()
        elapsed = (now - last_attempt.submitted_at).total_seconds()
        remaining = COOLDOWN_SECONDS - elapsed
        if remaining > 0:
            wait = math.ceil(remaining)
            return EligibilityResult(
                allowed=False,
                wait_seconds=max(wait, 1),
                next_attempt_number=next_attempt_number,
            )

    return EligibilityResult(
        allowed=True,
        wait_seconds=0,
        next_attempt_number=next_attempt_number,
        reason='',
    )


def record_assessment(
    user: 'AbstractUser',
    course_id: str,
    assessment_type: str,
    score: int,
    total: int,
    idempotency_key: Optional[uuid.UUID] = None,
    assessment_key: str = '',
    question_results: Optional[list] = None,
) -> tuple['UberLearnAssessmentAttempt', Optional[bool]]:
    """
    Persist an assessment attempt and return (assessment_obj, passed).

    The server computes passed based on PASS_THRESHOLDS — the client's claim
    is ignored. Raises ValueError if score > total.

    For baseline: passed is None (informational, LD-5).
    For final / retention: passed is True if score/total >= threshold.

    Parameters
    ----------
    user:
        The learner.
    course_id:
        Course key string.
    assessment_type:
        One of: 'baseline', 'final', 'retention'.
    score:
        Number of correct answers (integer, 0 <= score <= total).
    total:
        Total number of questions.
    idempotency_key:
        Client-generated UUID for replay protection. Optional for v1 compatibility.
    assessment_key:
        Usage key of the assessment block (snapshot at scoring time).
    question_results:
        Per-question results list [{usage_key, correct}].
    """
    from lms.djangoapps.uber_learn.models import UberLearnAssessmentAttempt  # noqa: PLC0415

    if score > total:
        raise ValueError(f"score ({score}) must not exceed total ({total})")

    threshold = PASS_THRESHOLDS.get(assessment_type)
    if assessment_type == 'baseline':
        passed: Optional[bool] = None
    else:
        # Server computes pass/fail — client cannot forge this
        passed = total > 0 and (score / total) >= threshold

    eligibility = check_assessment_eligibility(user, course_id, assessment_type)
    if not eligibility.allowed:
        if eligibility.reason == 'already_passed':
            raise AlreadyPassedError()
        raise CooldownActiveError(eligibility.wait_seconds)

    attempt_number = eligibility.next_attempt_number
    idem_key = idempotency_key or uuid.uuid4()

    try:
        with transaction.atomic():
            attempt = UberLearnAssessmentAttempt.objects.create(
                user=user,
                course_key=course_id,
                assessment_type=assessment_type,
                assessment_key=assessment_key or '',
                attempt_number=attempt_number,
                idempotency_key=idem_key,
                correct_count=score,
                total_count=total,
                passed=passed,
                question_results=question_results or [],
            )
    except IntegrityError:
        # Concurrent attempt took attempt_number. Replay the idempotency key if possible.
        existing = UberLearnAssessmentAttempt.objects.filter(
            user=user, idempotency_key=idem_key
        ).first()
        if existing:
            return existing, existing.passed
        raise ConcurrentSubmissionError() from None

    return attempt, passed


class CooldownActiveError(Exception):
    """Raised when an assessment attempt is blocked by cooldown."""

    def __init__(self, wait_seconds: int) -> None:
        self.wait_seconds = wait_seconds
        super().__init__(f"Assessment cooldown: {wait_seconds}s remaining")


class AlreadyPassedError(Exception):
    """Raised when attempting to retake an assessment the user has already passed."""


class ConcurrentSubmissionError(Exception):
    """Raised when a concurrent request claimed the same attempt_number."""
