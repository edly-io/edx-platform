"""
Unit tests for uber_learn models.

Tests the three new tables:
  - UberLearnActivityProgress  (one row per user+course+activity)
  - UberLearnAssessmentAttempt (one row per assessment attempt)
  - UberLearnBadgeAward        (sticky: written once per user+course+badge)
"""
import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from lms.djangoapps.uber_learn.models import (
    UberLearnActivityProgress,
    UberLearnAssessmentAttempt,
    UberLearnBadgeAward,
)

User = get_user_model()

COURSE_KEY = 'course-v1:TestOrg+TC101+2024'
ACTIVITY_KEY = 'block-v1:TestOrg+TC101+2024+type@vertical+block@abc'


class UberLearnActivityProgressModelTest(TestCase):
    """Tests for UberLearnActivityProgress model constraints and defaults."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='pw'
        )

    def test_create_activity_defaults(self):
        """An activity row is created with correct default values."""
        row = UberLearnActivityProgress.objects.create(
            user=self.user,
            course_key=COURSE_KEY,
            activity_key=ACTIVITY_KEY,
        )
        assert row.completed_at is None
        assert row.points_awarded == 0
        assert row.attempt_count == 0
        assert row.last_correct is None
        assert row.last_submitted_at is None

    def test_unique_constraint(self):
        """A second row with the same (user, course_key, activity_key) raises IntegrityError."""
        UberLearnActivityProgress.objects.create(
            user=self.user,
            course_key=COURSE_KEY,
            activity_key=ACTIVITY_KEY,
        )
        with self.assertRaises(IntegrityError):
            UberLearnActivityProgress.objects.create(
                user=self.user,
                course_key=COURSE_KEY,
                activity_key=ACTIVITY_KEY,
            )

    def test_str_representation(self):
        """__str__ includes user_id, course_key and activity_key."""
        row = UberLearnActivityProgress(
            user=self.user,
            course_key=COURSE_KEY,
            activity_key=ACTIVITY_KEY,
        )
        as_str = str(row)
        assert COURSE_KEY in as_str
        assert ACTIVITY_KEY in as_str

    def test_completed_at_can_be_set(self):
        """Setting completed_at and points_awarded works correctly."""
        row = UberLearnActivityProgress.objects.create(
            user=self.user,
            course_key=COURSE_KEY,
            activity_key=ACTIVITY_KEY,
        )
        now = timezone.now()
        UberLearnActivityProgress.objects.filter(pk=row.pk, completed_at__isnull=True).update(
            completed_at=now,
            points_awarded=10,
        )
        row.refresh_from_db()
        assert row.completed_at is not None
        assert row.points_awarded == 10

    def test_conditional_update_is_idempotent(self):
        """Conditional UPDATE on completed_at=NULL returns 0 when already completed."""
        row = UberLearnActivityProgress.objects.create(
            user=self.user,
            course_key=COURSE_KEY,
            activity_key=ACTIVITY_KEY,
        )
        now = timezone.now()
        first_update = UberLearnActivityProgress.objects.filter(
            pk=row.pk, completed_at__isnull=True
        ).update(completed_at=now, points_awarded=10)
        second_update = UberLearnActivityProgress.objects.filter(
            pk=row.pk, completed_at__isnull=True
        ).update(completed_at=now, points_awarded=10)

        assert first_update == 1
        assert second_update == 0  # already completed: WHERE condition fails


class UberLearnAssessmentAttemptModelTest(TestCase):
    """Tests for UberLearnAssessmentAttempt model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser2', email='test2@example.com', password='pw'
        )

    def _attempt(self, attempt_number=1, passed=None, idem_key=None):
        return UberLearnAssessmentAttempt.objects.create(
            user=self.user,
            course_key=COURSE_KEY,
            assessment_type='final',
            assessment_key='block-v1:Org+C+R+type@sequential+block@final',
            attempt_number=attempt_number,
            idempotency_key=idem_key or uuid.uuid4(),
            correct_count=4,
            total_count=5,
            passed=passed,
            question_results=[],
        )

    def test_create_attempt(self):
        """An assessment attempt row is created with the supplied values."""
        attempt = self._attempt(attempt_number=1, passed=True)
        assert attempt.pk is not None
        assert attempt.passed is True
        assert attempt.correct_count == 4
        assert attempt.total_count == 5

    def test_baseline_passed_is_nullable(self):
        """Baseline attempt can have passed=None."""
        attempt = UberLearnAssessmentAttempt.objects.create(
            user=self.user,
            course_key=COURSE_KEY,
            assessment_type='baseline',
            assessment_key='block-v1:Org+C+R+type@sequential+block@baseline',
            attempt_number=1,
            idempotency_key=uuid.uuid4(),
            correct_count=3,
            total_count=5,
            passed=None,
            question_results=[],
        )
        assert attempt.passed is None

    def test_unique_attempt_number_per_user_course_type(self):
        """Two rows with the same (user, course_key, type, attempt_number) raise IntegrityError."""
        same_key = uuid.uuid4()
        self._attempt(attempt_number=1)
        with self.assertRaises(IntegrityError):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user,
                course_key=COURSE_KEY,
                assessment_type='final',
                assessment_key='block-v1:...',
                attempt_number=1,   # duplicate
                idempotency_key=uuid.uuid4(),
                correct_count=2,
                total_count=5,
                passed=False,
                question_results=[],
            )

    def test_idempotency_key_unique_per_user(self):
        """Same idempotency_key for the same user raises IntegrityError."""
        idem_key = uuid.uuid4()
        self._attempt(attempt_number=1, idem_key=idem_key)
        with self.assertRaises(IntegrityError):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user,
                course_key=COURSE_KEY,
                assessment_type='final',
                assessment_key='block-v1:...',
                attempt_number=2,
                idempotency_key=idem_key,  # duplicate
                correct_count=4,
                total_count=5,
                passed=True,
                question_results=[],
            )

    def test_multiple_attempts_allowed(self):
        """Multiple assessment attempts are allowed with different attempt_numbers."""
        for i in range(3):
            self._attempt(attempt_number=i + 1)
        count = UberLearnAssessmentAttempt.objects.filter(
            user=self.user, course_key=COURSE_KEY, assessment_type='final'
        ).count()
        assert count == 3


class UberLearnBadgeAwardModelTest(TestCase):
    """Tests for UberLearnBadgeAward model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser3', email='test3@example.com', password='pw'
        )

    def test_create_badge_award(self):
        """A badge award row is created correctly."""
        award = UberLearnBadgeAward.objects.create(
            user=self.user,
            course_key=COURSE_KEY,
            badge_type='applied',
        )
        assert award.pk is not None
        assert award.badge_type == 'applied'

    def test_unique_per_user_course_badge(self):
        """Duplicate (user, course_key, badge_type) raises IntegrityError."""
        UberLearnBadgeAward.objects.create(
            user=self.user,
            course_key=COURSE_KEY,
            badge_type='applied',
        )
        with self.assertRaises(IntegrityError):
            UberLearnBadgeAward.objects.create(
                user=self.user,
                course_key=COURSE_KEY,
                badge_type='applied',   # duplicate
            )

    def test_different_badge_types_for_same_user_allowed(self):
        """Different badge types for the same user/course are allowed."""
        UberLearnBadgeAward.objects.create(
            user=self.user, course_key=COURSE_KEY, badge_type='applied'
        )
        UberLearnBadgeAward.objects.create(
            user=self.user, course_key=COURSE_KEY, badge_type='thorough'
        )
        count = UberLearnBadgeAward.objects.filter(
            user=self.user, course_key=COURSE_KEY
        ).count()
        assert count == 2
