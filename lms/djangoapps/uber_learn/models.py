"""
Uber Learn progress models.

Three new tables, all purely additive (no existing table is changed):
  - UberLearnActivityProgress  (one row per user+course+activity)
  - UberLearnAssessmentAttempt (one row per assessment attempt, append-only)
  - UberLearnBadgeAward        (sticky: written once, deleted only on retirement)

See solution design: docs/architecture/uber-learn-progress-api-solution-design.md

.. no_pii:
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class AssessmentType(models.TextChoices):
    BASELINE = "baseline", "Baseline"
    FINAL = "final", "Final"
    RETENTION = "retention", "Retention"


class BadgeType(models.TextChoices):
    APPLIED = "applied", "Applied"
    THOROUGH = "thorough", "Thorough"
    RETAINED = "retained", "Retained"


class UberLearnActivityProgress(models.Model):
    """
    One row per (user, course, activity).  An activity is a VerticalBlock (LD-2).

    Created on the first submission for the activity.  Points are awarded exactly
    once, by an atomic conditional UPDATE that moves completed_at from NULL to a
    non-NULL value.

    Completion is STICKY: a later incorrect submission never clears completed_at
    or points_awarded.

    .. no_pii:
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='+',
    )
    course_key = models.CharField(max_length=255, db_index=True)
    activity_key = models.CharField(max_length=255)   # vertical usage key

    attempt_count = models.PositiveIntegerField(default=0)    # analytics only
    last_submitted_at = models.DateTimeField(null=True, blank=True)
    # NULL = non-scorable (video/reading); True/False = scorable.
    last_correct = models.BooleanField(null=True, blank=True)

    # first VALID completion; never cleared once set
    completed_at = models.DateTimeField(null=True, blank=True)
    # 0 or ACTIVITY_POINTS; set once by the conditional UPDATE
    points_awarded = models.PositiveSmallIntegerField(default=0)

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'uber_learn'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'course_key', 'activity_key'],
                name='uber_learn_activity_user_course_activity_uniq',
            ),
        ]

    def __str__(self) -> str:
        return (
            f"UberLearnActivityProgress("
            f"user={self.user_id}, course={self.course_key}, "
            f"key={self.activity_key}, completed_at={self.completed_at})"
        )


class UberLearnAssessmentAttempt(models.Model):
    """
    One row per scored assessment attempt.  Append-only: rows are never updated.

    Request body is {assessment_type, idempotency_key} only.  No score is
    submitted by the client; the server reads StudentModule.

    .. no_pii:
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='+',
    )
    course_key = models.CharField(max_length=255, db_index=True)
    assessment_type = models.CharField(
        max_length=16,
        choices=AssessmentType.choices,
    )
    # Usage key of the configured assessment block (snapshot at scoring time)
    assessment_key = models.CharField(max_length=255)
    # 1-based, per (user, course, type)
    attempt_number = models.PositiveIntegerField()
    # Client-generated UUID per "Submit assessment" click; reused on retries
    idempotency_key = models.UUIDField()

    correct_count = models.PositiveSmallIntegerField()
    total_count = models.PositiveSmallIntegerField()
    # NULL for baseline (informational per LD-5)
    passed = models.BooleanField(null=True)
    # [{"usage_key": str, "correct": bool}]
    question_results = models.JSONField(default=list)
    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = 'uber_learn'
        constraints = [
            # Serializes concurrent attempts: two racers computing the same N+1
            # -> one IntegrityError.
            models.UniqueConstraint(
                fields=['user', 'course_key', 'assessment_type', 'attempt_number'],
                name='uber_learn_assessment_attempt_number_uniq',
            ),
            # Replay protection for network retries.
            models.UniqueConstraint(
                fields=['user', 'idempotency_key'],
                name='uber_learn_assessment_idempotency_uniq',
            ),
        ]
        indexes = [
            models.Index(
                fields=['user', 'course_key', 'assessment_type', 'passed'],
                name='uber_learn_assess_pass_idx',
            ),
        ]

    def __str__(self) -> str:
        return (
            f"UberLearnAssessmentAttempt("
            f"user={self.user_id}, course={self.course_key}, "
            f"type={self.assessment_type}, attempt={self.attempt_number}, "
            f"score={self.correct_count}/{self.total_count}, passed={self.passed})"
        )


class UberLearnBadgeAward(models.Model):
    """
    Sticky badge awards.  Once written, never deleted except by user retirement.

    Badge types:
      - applied:   final assessment passed
      - thorough:  applied AND all activities complete
      - retained:  retention assessment passed

    Named *Award to avoid confusion with lms/djangoapps/badges.

    .. no_pii:
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='+',
    )
    course_key = models.CharField(max_length=255, db_index=True)
    badge_type = models.CharField(max_length=16, choices=BadgeType.choices)
    awarded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = 'uber_learn'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'course_key', 'badge_type'],
                name='uber_learn_badge_user_course_type_uniq',
            ),
        ]

    def __str__(self) -> str:
        return (
            f"UberLearnBadgeAward("
            f"user={self.user_id}, course={self.course_key}, "
            f"badge={self.badge_type}, awarded_at={self.awarded_at})"
        )
