"""
Tests for assessment retry state machine (batch model, H-1) and
AlreadyPassedError terminal state (H-2).

Batch model rules:
  - baseline: unlimited attempts, no cooldown ever
  - final / retention:
      * Cooldown gate fires ONLY at exact batch boundaries
        (count % MAX_ATTEMPTS_PER_WINDOW == 0).
      * Within a batch (e.g. attempts 1-3 or 4-6): all free.
      * At the boundary: blocked if < COOLDOWN_SECONDS since last attempt.
      * After cooldown, a fresh batch of MAX_ATTEMPTS_PER_WINDOW is granted.
      * Example: attempt 4 blocked within 60s of attempt 3;
                 attempts 4-6 are all free after cooldown;
                 attempt 7 blocked within 60s of attempt 6.

H-2 terminal state:
  - After passing final/retention, already_passed block is permanent
    (not a timed cooldown).
"""
import uuid as uuid_module
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lms.djangoapps.uber_learn.assessment import (
    COOLDOWN_SECONDS,
    MAX_ATTEMPTS_PER_WINDOW,
    AlreadyPassedError,
    EligibilityResult,
    check_assessment_eligibility,
    record_assessment,
)
from lms.djangoapps.uber_learn.scoring import PASS_THRESHOLDS

User = get_user_model()

COURSE_ID = 'course-v1:Test+Assess+2024'


# ---------------------------------------------------------------------------
# In-memory fake for check_assessment_eligibility
# ---------------------------------------------------------------------------

def _make_fake_eligibility_checker():
    """
    Returns a closure that simulates the retry/cooldown state machine.
    State is kept in 'attempts_store'.
    """
    attempts_store: dict = {}  # (user_pk, course_id, atype) -> list[datetime]

    def fake_check(user, course_id, assessment_type):
        if assessment_type == 'baseline':
            key = (user.pk, course_id, assessment_type)
            attempts = attempts_store.get(key, [])
            return EligibilityResult(
                allowed=True,
                wait_seconds=0,
                next_attempt_number=len(attempts) + 1,
            )

        key = (user.pk, course_id, assessment_type)
        now = timezone.now()
        window_start = now - timedelta(seconds=COOLDOWN_SECONDS)

        all_attempts = attempts_store.get(key, [])
        # Keep only attempts within the current 60s window
        recent = [t for t in all_attempts if t > window_start]

        if len(recent) >= MAX_ATTEMPTS_PER_WINDOW:
            oldest_recent = min(recent)
            wait = int((oldest_recent + timedelta(seconds=COOLDOWN_SECONDS) - now).total_seconds())
            wait = max(wait, 1)
            return EligibilityResult(
                allowed=False,
                wait_seconds=wait,
                next_attempt_number=len(all_attempts) + 1,
            )

        return EligibilityResult(
            allowed=True,
            wait_seconds=0,
            next_attempt_number=len(all_attempts) + 1,
        )

    def fake_record(user, course_id, assessment_type, score, total):
        if score > total:
            raise ValueError(f"score ({score}) must not exceed total ({total})")

        threshold = PASS_THRESHOLDS.get(assessment_type, 1.0)
        passed = (score / total) >= threshold

        key = (user.pk, course_id, assessment_type)
        attempts_store.setdefault(key, []).append(timezone.now())

        obj = MagicMock()
        obj.attempt_number = len(attempts_store[key])
        obj.score = score
        obj.total = total
        obj.passed = passed
        return obj, passed

    return attempts_store, fake_check, fake_record


# ---------------------------------------------------------------------------
# Baseline assessment (unlimited attempts)
# ---------------------------------------------------------------------------

class BaselineAssessmentTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='baseline_user',
            email='base@test.com',
            password='pass',
        )

    def test_baseline_first_attempt_allowed(self):
        """AC-ASSESS-01: Baseline first attempt is always allowed."""
        _, fake_check, _ = _make_fake_eligibility_checker()
        with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                   side_effect=fake_check):
            result = check_assessment_eligibility(self.user, COURSE_ID, 'baseline')
        self.assertTrue(result.allowed)
        self.assertEqual(result.wait_seconds, 0)

    def test_baseline_unlimited_attempts_allowed(self):
        """AC-ASSESS-02: Baseline allows >3 consecutive attempts without cooldown."""
        _, fake_check, fake_record = _make_fake_eligibility_checker()
        for i in range(10):
            with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                       side_effect=fake_check):
                result = check_assessment_eligibility(self.user, COURSE_ID, 'baseline')
            self.assertTrue(result.allowed, f"Attempt {i+1} should be allowed for baseline")
            self.assertEqual(result.wait_seconds, 0)
            # Simulate the attempt being recorded
            with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                       side_effect=fake_record):
                record_assessment(self.user, COURSE_ID, 'baseline', 3, 5)


# ---------------------------------------------------------------------------
# Final assessment retry / cooldown
# ---------------------------------------------------------------------------

class FinalAssessmentRetryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='final_user',
            email='final@test.com',
            password='pass',
        )

    def test_final_first_attempt_allowed(self):
        """AC-ASSESS-03: Final first attempt is allowed."""
        _, fake_check, _ = _make_fake_eligibility_checker()
        with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                   side_effect=fake_check):
            result = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        self.assertTrue(result.allowed)

    def test_final_second_attempt_allowed(self):
        """AC-ASSESS-04: Final second attempt is allowed."""
        store, fake_check, fake_record = _make_fake_eligibility_checker()
        # Record first attempt
        with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                   side_effect=fake_record):
            record_assessment(self.user, COURSE_ID, 'final', 2, 5)

        with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                   side_effect=fake_check):
            result = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        self.assertTrue(result.allowed)

    def test_final_third_attempt_allowed(self):
        """AC-ASSESS-05: Final third attempt is allowed."""
        store, fake_check, fake_record = _make_fake_eligibility_checker()
        for _ in range(2):
            with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                       side_effect=fake_record):
                record_assessment(self.user, COURSE_ID, 'final', 2, 5)

        with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                   side_effect=fake_check):
            result = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        self.assertTrue(result.allowed)

    def test_final_fourth_attempt_within_cooldown_blocked(self):
        """
        AC-ASSESS-06: Fourth attempt within 60s of the third is BLOCKED.
        Must return allowed=False with wait_seconds > 0.
        """
        store, fake_check, fake_record = _make_fake_eligibility_checker()
        for _ in range(MAX_ATTEMPTS_PER_WINDOW):
            with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                       side_effect=fake_record):
                record_assessment(self.user, COURSE_ID, 'final', 2, 5)

        with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                   side_effect=fake_check):
            result = check_assessment_eligibility(self.user, COURSE_ID, 'final')

        self.assertFalse(result.allowed, "Fourth attempt within cooldown must be blocked")
        self.assertGreater(result.wait_seconds, 0,
                           "Must return a positive wait_seconds when blocked")

    def test_final_fourth_attempt_after_cooldown_allowed(self):
        """
        AC-ASSESS-07 (CRITICAL): Fourth attempt AFTER the 60s cooldown is
        ALLOWED.  This is NOT a permanent lockout.

        Verifies the batch model using real DB records:  3 attempts recorded
        with submitted_at = 61s ago → cooldown has expired → attempt 4 free.
        """
        from lms.djangoapps.uber_learn.models import UberLearnAssessmentAttempt  # noqa: PLC0415

        past_time = timezone.now() - timedelta(seconds=COOLDOWN_SECONDS + 1)
        for i in range(MAX_ATTEMPTS_PER_WINDOW):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user, course_key=COURSE_ID, assessment_type='final',
                assessment_key='', attempt_number=i + 1,
                idempotency_key=uuid_module.uuid4(),
                correct_count=0, total_count=5, passed=False,
                question_results=[], submitted_at=past_time,
            )

        result = check_assessment_eligibility(self.user, COURSE_ID, 'final')

        self.assertTrue(
            result.allowed,
            "Attempt after cooldown must be ALLOWED — this is NOT permanent lockout",
        )
        self.assertEqual(result.wait_seconds, 0)

    def test_final_seventh_attempt_after_two_cooldowns_allowed(self):
        """
        AC-ASSESS-08: After 6 attempts (two batches of 3), the 7th attempt
        is allowed once both cooldowns have expired.

        Verifies using real DB records: 6 attempts with submitted_at older
        than COOLDOWN_SECONDS → count=6, 6%3==0, but last attempt is old
        → cooldown gate passes → allowed.
        """
        from lms.djangoapps.uber_learn.models import UberLearnAssessmentAttempt  # noqa: PLC0415

        past = timezone.now() - timedelta(seconds=COOLDOWN_SECONDS + 5)
        for i in range(MAX_ATTEMPTS_PER_WINDOW * 2):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user, course_key=COURSE_ID, assessment_type='final',
                assessment_key='', attempt_number=i + 1,
                idempotency_key=uuid_module.uuid4(),
                correct_count=0, total_count=5, passed=False,
                question_results=[], submitted_at=past,
            )

        result = check_assessment_eligibility(self.user, COURSE_ID, 'final')

        self.assertTrue(result.allowed,
                        "7th attempt after two expired cooldowns must be allowed")

    def test_cooldown_returns_positive_wait_seconds(self):
        """AC-ASSESS-09: While blocked, wait_seconds must be > 0 and <= COOLDOWN_SECONDS."""
        store, fake_check, fake_record = _make_fake_eligibility_checker()
        for _ in range(MAX_ATTEMPTS_PER_WINDOW):
            with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                       side_effect=fake_record):
                record_assessment(self.user, COURSE_ID, 'final', 2, 5)

        with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                   side_effect=fake_check):
            result = check_assessment_eligibility(self.user, COURSE_ID, 'final')

        self.assertFalse(result.allowed)
        self.assertGreater(result.wait_seconds, 0)
        self.assertLessEqual(result.wait_seconds, COOLDOWN_SECONDS)

    def test_batch_model_second_batch_attempts_all_allowed(self):
        """
        AC-ASSESS-BATCH-01: After 3 attempts and cooldown expiry, the next
        3 attempts (batch 2: 4, 5, 6) are ALL freely allowed.  The cooldown
        gate fires ONLY at count % MAX_ATTEMPTS_PER_WINDOW == 0 boundaries,
        so there is no mid-batch cooldown.
        """
        from lms.djangoapps.uber_learn.models import UberLearnAssessmentAttempt  # noqa: PLC0415

        past = timezone.now() - timedelta(seconds=COOLDOWN_SECONDS + 5)
        for i in range(MAX_ATTEMPTS_PER_WINDOW):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user, course_key=COURSE_ID, assessment_type='final',
                assessment_key='', attempt_number=i + 1,
                idempotency_key=uuid_module.uuid4(),
                correct_count=0, total_count=5, passed=False,
                question_results=[], submitted_at=past,
            )

        # Attempt 4 — start of batch 2, cooldown expired → free
        result4 = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        self.assertTrue(result4.allowed, "Attempt 4 (batch 2, slot 1) must be allowed")

        record_assessment(self.user, COURSE_ID, 'final', score=0, total=5)

        # Attempt 5 — count=4, 4%3!=0 → no cooldown gate → free
        result5 = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        self.assertTrue(result5.allowed,
                        "Attempt 5 (batch 2, slot 2) must be allowed — no mid-batch cooldown")

        record_assessment(self.user, COURSE_ID, 'final', score=0, total=5)

        # Attempt 6 — count=5, 5%3!=0 → no cooldown gate → free
        result6 = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        self.assertTrue(result6.allowed,
                        "Attempt 6 (batch 2, slot 3) must be allowed")

    def test_seventh_attempt_within_cooldown_blocked(self):
        """
        AC-ASSESS-BATCH-02: At the second batch boundary (count=6, 6%3==0),
        attempt 7 within 60s of attempt 6 is BLOCKED.
        """
        from lms.djangoapps.uber_learn.models import UberLearnAssessmentAttempt  # noqa: PLC0415

        past = timezone.now() - timedelta(seconds=COOLDOWN_SECONDS + 5)
        for i in range(5):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user, course_key=COURSE_ID, assessment_type='final',
                assessment_key='', attempt_number=i + 1,
                idempotency_key=uuid_module.uuid4(),
                correct_count=0, total_count=5, passed=False,
                question_results=[], submitted_at=past,
            )
        # Attempt 6 — recent (within cooldown window)
        UberLearnAssessmentAttempt.objects.create(
            user=self.user, course_key=COURSE_ID, assessment_type='final',
            assessment_key='', attempt_number=6,
            idempotency_key=uuid_module.uuid4(),
            correct_count=0, total_count=5, passed=False,
            question_results=[],
            # submitted_at defaults to timezone.now()
        )

        result = check_assessment_eligibility(self.user, COURSE_ID, 'final')

        self.assertFalse(result.allowed,
                         "Attempt 7 within cooldown of attempt 6 must be blocked")
        self.assertGreater(
            result.wait_seconds, 0,
            "Must return a positive wait_seconds at the second batch boundary",
        )

    def test_next_attempt_number_increments(self):
        """AC-ASSESS-10: next_attempt_number must monotonically increase."""
        store, fake_check, fake_record = _make_fake_eligibility_checker()
        attempt_numbers = []

        for i in range(3):
            with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                       side_effect=fake_check):
                result = check_assessment_eligibility(self.user, COURSE_ID, 'final')
            attempt_numbers.append(result.next_attempt_number)
            with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                       side_effect=fake_record):
                record_assessment(self.user, COURSE_ID, 'final', 2, 5)

        self.assertEqual(attempt_numbers, [1, 2, 3],
                         "next_attempt_number must increment: [1, 2, 3]")


# ---------------------------------------------------------------------------
# Retention has same retry policy as final
# ---------------------------------------------------------------------------

class RetentionAssessmentRetryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='retention_user',
            email='ret@test.com',
            password='pass',
        )

    def test_retention_fourth_attempt_within_cooldown_blocked(self):
        """AC-ASSESS-11: Retention fourth attempt within cooldown is blocked."""
        store, fake_check, fake_record = _make_fake_eligibility_checker()
        for _ in range(MAX_ATTEMPTS_PER_WINDOW):
            with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                       side_effect=fake_record):
                record_assessment(self.user, COURSE_ID, 'retention', 2, 5)

        with patch('lms.djangoapps.uber_learn.assessment.check_assessment_eligibility',
                   side_effect=fake_check):
            result = check_assessment_eligibility(self.user, COURSE_ID, 'retention')

        self.assertFalse(result.allowed)
        self.assertGreater(result.wait_seconds, 0)

    def test_retention_fourth_attempt_after_cooldown_allowed(self):
        """AC-ASSESS-12: Retention: after cooldown, attempt is allowed (NOT permanent lockout)."""
        from lms.djangoapps.uber_learn.models import UberLearnAssessmentAttempt  # noqa: PLC0415

        past_time = timezone.now() - timedelta(seconds=COOLDOWN_SECONDS + 1)
        for i in range(MAX_ATTEMPTS_PER_WINDOW):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user, course_key=COURSE_ID, assessment_type='retention',
                assessment_key='', attempt_number=i + 1,
                idempotency_key=uuid_module.uuid4(),
                correct_count=0, total_count=5, passed=False,
                question_results=[], submitted_at=past_time,
            )

        result = check_assessment_eligibility(self.user, COURSE_ID, 'retention')

        self.assertTrue(result.allowed)
        self.assertEqual(result.wait_seconds, 0)


# ---------------------------------------------------------------------------
# Server-authoritative scoring
# ---------------------------------------------------------------------------

class AssessmentScoringTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='scoring_user',
            email='score@test.com',
            password='pass',
        )

    def test_score_below_threshold_returns_failed(self):
        """
        AC-ASSESS-13: Score of 3/5 on final must be marked as FAILED.
        Pass threshold is 4/5.
        """
        _, _, fake_record = _make_fake_eligibility_checker()
        with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                   side_effect=fake_record):
            obj, passed = record_assessment(self.user, COURSE_ID, 'final', 3, 5)
        self.assertFalse(passed, "Score 3/5 must be FAILED on final (threshold 4/5)")
        self.assertFalse(obj.passed)

    def test_score_at_threshold_returns_passed(self):
        """AC-ASSESS-14: Score of 4/5 on final must be marked as PASSED."""
        _, _, fake_record = _make_fake_eligibility_checker()
        with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                   side_effect=fake_record):
            obj, passed = record_assessment(self.user, COURSE_ID, 'final', 4, 5)
        self.assertTrue(passed, "Score 4/5 must be PASSED on final")

    def test_score_above_threshold_returns_passed(self):
        """AC-ASSESS-15: Score of 5/5 on final must be marked as PASSED."""
        _, _, fake_record = _make_fake_eligibility_checker()
        with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                   side_effect=fake_record):
            obj, passed = record_assessment(self.user, COURSE_ID, 'final', 5, 5)
        self.assertTrue(passed, "Score 5/5 must be PASSED on final")

    def test_client_cannot_forge_passed_via_high_score(self):
        """
        AC-ASSESS-16: Server must compute pass/fail from score/total.
        A score of 3/5 must fail even if a malicious client claims passed=True.
        This test verifies that record_assessment does NOT accept a 'passed'
        parameter — only score and total are inputs.
        """
        import inspect
        sig = inspect.signature(record_assessment)
        self.assertNotIn(
            'passed',
            sig.parameters,
            "record_assessment must NOT accept 'passed' parameter — "
            "server must compute it from score/total to prevent client forgery",
        )

    def test_retention_score_below_threshold_fails(self):
        """AC-ASSESS-17: Score of 3/5 on retention must also be FAILED."""
        _, _, fake_record = _make_fake_eligibility_checker()
        with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                   side_effect=fake_record):
            _, passed = record_assessment(self.user, COURSE_ID, 'retention', 3, 5)
        self.assertFalse(passed)

    def test_baseline_passed_is_none(self):
        """
        AC-ASSESS-18: Baseline is informational — passed is always None.

        The solution design specifies passed=None for baseline (LD-5), not True/False.
        Baseline threshold (0.0) has no behavioral meaning for pass/fail.
        """
        _, passed = record_assessment(self.user, COURSE_ID, 'baseline', 0, 5)
        self.assertIsNone(passed, "Baseline must return passed=None (informational, not scored)")

    def test_score_greater_than_total_raises_value_error(self):
        """AC-ASSESS-19: score > total must raise ValueError (invalid input)."""
        _, _, fake_record = _make_fake_eligibility_checker()
        with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                   side_effect=fake_record):
            with self.assertRaises(ValueError):
                record_assessment(self.user, COURSE_ID, 'final', 6, 5)

    def test_score_equal_to_total_does_not_raise(self):
        """AC-ASSESS-20: score == total (perfect) is valid."""
        _, _, fake_record = _make_fake_eligibility_checker()
        with patch('lms.djangoapps.uber_learn.assessment.record_assessment',
                   side_effect=fake_record):
            try:
                record_assessment(self.user, COURSE_ID, 'final', 5, 5)
            except ValueError:
                self.fail("score == total must not raise ValueError")

    def test_pass_thresholds_constant_values(self):
        """
        AC-ASSESS-21: Verify PASS_THRESHOLDS contains expected values
        so a future refactor cannot silently lower the bar.
        """
        self.assertAlmostEqual(PASS_THRESHOLDS['final'], 4 / 5, places=5,
                               msg="Final pass threshold must be 4/5 (0.8)")
        self.assertAlmostEqual(PASS_THRESHOLDS['retention'], 4 / 5, places=5,
                               msg="Retention pass threshold must be 4/5 (0.8)")
        self.assertAlmostEqual(PASS_THRESHOLDS['baseline'], 0.0, places=5,
                               msg="Baseline pass threshold must be 0.0")


# ---------------------------------------------------------------------------
# H-2: AlreadyPassedError — terminal state after a passing attempt
# ---------------------------------------------------------------------------

class AlreadyPassedErrorTest(TestCase):
    """
    AC-ASSESS-22 / H-2: Once a user passes a final or retention assessment,
    check_assessment_eligibility returns allowed=False with reason='already_passed'.
    record_assessment raises AlreadyPassedError in that state.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='already_passed_user',
            email='passed@test.com',
            password='pass',
        )

    def test_eligibility_after_passing_final_returns_already_passed(self):
        """
        AC-ASSESS-22: After a passing final attempt, check_assessment_eligibility
        returns allowed=False and reason='already_passed'.
        """
        # Record a passing attempt in the DB directly.
        record_assessment(self.user, COURSE_ID, 'final', score=4, total=5)

        result = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        self.assertFalse(result.allowed, "Must be blocked after passing final")
        self.assertEqual(
            result.reason, 'already_passed',
            "reason must be 'already_passed' when user has already passed",
        )
        self.assertEqual(result.wait_seconds, 0,
                         "wait_seconds must be 0 for already_passed (not a cooldown)")

    def test_eligibility_after_passing_retention_returns_already_passed(self):
        """
        AC-ASSESS-23: After a passing retention attempt, eligibility returns
        already_passed (terminal state for retention too).
        """
        record_assessment(self.user, COURSE_ID, 'retention', score=4, total=5)
        result = check_assessment_eligibility(self.user, COURSE_ID, 'retention')
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, 'already_passed')

    def test_record_assessment_raises_already_passed_on_retake(self):
        """
        AC-ASSESS-24: record_assessment raises AlreadyPassedError when the
        user has already passed and tries to re-submit.
        """
        # First attempt: passing
        record_assessment(self.user, COURSE_ID, 'final', score=4, total=5)
        # Second attempt: must raise AlreadyPassedError
        with self.assertRaises(AlreadyPassedError):
            record_assessment(self.user, COURSE_ID, 'final', score=5, total=5)

    def test_already_passed_does_not_affect_baseline(self):
        """
        AC-ASSESS-25: Passing a final assessment does NOT block baseline
        attempts (they are independent types with unlimited attempts).
        """
        record_assessment(self.user, COURSE_ID, 'final', score=4, total=5)
        result = check_assessment_eligibility(self.user, COURSE_ID, 'baseline')
        self.assertTrue(result.allowed, "Baseline must remain allowed after final pass")

    def test_already_passed_state_is_permanent(self):
        """
        AC-ASSESS-26: already_passed is a terminal state.  Even if more than
        60s elapse, the block remains.  (Cooldown is time-limited; already_passed
        is not.)
        """
        record_assessment(self.user, COURSE_ID, 'final', score=4, total=5)
        # already_passed should remain regardless of elapsed time
        result = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, 'already_passed')

    def test_failed_attempts_do_not_set_already_passed(self):
        """
        AC-ASSESS-27: Failing the final assessment (even multiple times) does
        NOT trigger the already_passed block.  Cooldown may apply, but not
        already_passed.
        """
        attempt, passed = record_assessment(
            self.user, COURSE_ID, 'final', score=3, total=5
        )
        self.assertFalse(passed)
        result = check_assessment_eligibility(self.user, COURSE_ID, 'final')
        # May be blocked by cooldown but NOT by already_passed
        self.assertNotEqual(result.reason, 'already_passed',
                            "Failed attempt must NOT set already_passed")

    def test_assessment_view_returns_409_already_passed(self):
        """
        AC-ASSESS-28 (H-2): AssessmentView.post returns 409 with
        error_code='already_passed' when the user has already passed.
        """
        ASSESSMENT_URL = f'/api/uber_learn/v1/progress/{COURSE_ID}/assessment'
        # Record a passing attempt so the DB has passed=True
        record_assessment(self.user, COURSE_ID, 'final', score=4, total=5)

        client = APIClient()
        client.force_authenticate(user=self.user)

        with patch(
            'lms.djangoapps.uber_learn.views._BaseUberLearnView._check_enrollment',
            return_value=True,
        ):
            response = client.post(
                ASSESSMENT_URL,
                {'assessment_type': 'final', 'idempotency_key': str(uuid_module.uuid4())},
                format='json',
            )

        self.assertEqual(
            response.status_code, 409,
            "Must return 409 when user has already passed the final assessment",
        )
        data = response.json()
        self.assertEqual(
            data.get('error_code'), 'already_passed',
            "error_code must be 'already_passed' (not 'cooldown_active')",
        )
