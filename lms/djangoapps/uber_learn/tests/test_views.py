"""
API tests for the Uber Learn Progress endpoints.

Covers the backend business logic through the service layer with mocked
platform integration points (enrollment, block structure).

AC coverage:
  AC1  - GET /progress returns 403 if not enrolled
  AC2  - GET /progress returns aggregated data for enrolled user
  AC3  - POST /activity records completion and returns points_awarded_now=10 once
  AC4  - POST /activity with incorrect answer does NOT block later correct answer
  AC5  - POST /activity is idempotent — second call yields points_awarded_now=0
  AC6  - POST /activity returns 403 if not enrolled
  AC7  - POST /assessment applies server-side pass threshold
  AC8  - POST /assessment returns 429 after cooldown trigger
  AC9  - POST /assessment baseline never enters cooldown
  AC10 - POST /assessment with invalid score range returns 400
  AC11 - POST /activity with missing required fields returns 400
  AC12 - Unauthenticated requests return 401
"""
import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lms.djangoapps.uber_learn.models import (
    UberLearnActivityProgress,
    UberLearnAssessmentAttempt,
    UberLearnBadgeAward,
)
from lms.djangoapps.uber_learn.scoring import POINTS_PER_ACTIVITY, record_activity
from lms.djangoapps.uber_learn.assessment import (
    check_assessment_eligibility,
    record_assessment,
    EligibilityResult,
    CooldownActiveError,
)
from lms.djangoapps.uber_learn.badges import evaluate_and_award_badges

User = get_user_model()

COURSE_KEY = 'course-v1:TestOrg+TC101+2024'
ACTIVITY_KEY = 'block-v1:TestOrg+TC101+2024+type@vertical+block@abc123'
PROGRESS_URL = f'/api/uber_learn/v1/progress/{COURSE_KEY}'
ACTIVITY_URL = f'/api/uber_learn/v1/progress/{COURSE_KEY}/activity'
ASSESSMENT_URL = f'/api/uber_learn/v1/progress/{COURSE_KEY}/assessment'


class _EnrollmentMixin:
    """Mixin to mock enrollment check for view tests."""

    def _enrolled_client(self, username='apiuser'):
        user = User.objects.create_user(
            username=username, email=f'{username}@test.com', password='pw'
        )
        client = APIClient()
        client.force_authenticate(user=user)
        return client, user

    def _mock_enrolled(self, enrolled=True):
        return mock.patch(
            'lms.djangoapps.uber_learn.views._BaseUberLearnView._check_enrollment',
            return_value=enrolled,
        )


# ---------------------------------------------------------------------------
# Business-logic unit tests (no HTTP layer — test service functions directly)
# ---------------------------------------------------------------------------

class RecordActivityLogicTest(TestCase):
    """Tests for scoring.record_activity business logic with real DB."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='logic_user', email='logic@test.com', password='pw'
        )

    def test_video_first_completion_earns_points(self):
        """AC3: First video completion earns POINTS_PER_ACTIVITY."""
        pts, is_first = record_activity(
            self.user, COURSE_KEY, ACTIVITY_KEY, 'video', correct=None
        )
        assert pts == POINTS_PER_ACTIVITY
        assert is_first is True

    def test_video_second_completion_earns_zero(self):
        """AC5: Second identical video completion earns 0 additional points."""
        record_activity(self.user, COURSE_KEY, ACTIVITY_KEY, 'video', correct=None)
        pts, is_first = record_activity(
            self.user, COURSE_KEY, ACTIVITY_KEY, 'video', correct=None
        )
        assert pts == 0
        assert is_first is False

    def test_incorrect_then_correct_earns_points(self):
        """
        AC4: Incorrect first submission MUST NOT prevent a later correct one
        from earning points.
        """
        pts_first, _ = record_activity(
            self.user, COURSE_KEY, ACTIVITY_KEY, 'choice', correct=False
        )
        assert pts_first == 0

        row = UberLearnActivityProgress.objects.get(
            user=self.user, course_key=COURSE_KEY, activity_key=ACTIVITY_KEY
        )
        assert row.completed_at is None

        pts_second, is_first = record_activity(
            self.user, COURSE_KEY, ACTIVITY_KEY, 'choice', correct=True
        )
        assert pts_second == POINTS_PER_ACTIVITY
        assert is_first is True

        row.refresh_from_db()
        assert row.completed_at is not None
        assert row.points_awarded == POINTS_PER_ACTIVITY

    def test_choice_incorrect_earns_zero(self):
        """CAPA incorrect submission earns 0 points."""
        pts, is_first = record_activity(
            self.user, COURSE_KEY, ACTIVITY_KEY, 'choice', correct=False
        )
        assert pts == 0
        assert is_first is False

    def test_attempt_count_increments(self):
        """Attempt count increments on each call."""
        record_activity(self.user, COURSE_KEY, ACTIVITY_KEY, 'choice', correct=False)
        record_activity(self.user, COURSE_KEY, ACTIVITY_KEY, 'choice', correct=True)
        row = UberLearnActivityProgress.objects.get(
            user=self.user, course_key=COURSE_KEY, activity_key=ACTIVITY_KEY
        )
        assert row.attempt_count == 2

    def test_unknown_activity_type_raises(self):
        """AC11 (service layer): Unknown activity_type raises ValueError."""
        with self.assertRaises(ValueError):
            record_activity(
                self.user, COURSE_KEY, ACTIVITY_KEY, 'capa', correct=True
            )


class AssessmentEligibilityTest(TestCase):
    """Tests for assessment.check_assessment_eligibility."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='elig_user', email='elig@test.com', password='pw'
        )

    def test_baseline_always_allowed(self):
        """AC9: Baseline is always allowed."""
        result = check_assessment_eligibility(self.user, COURSE_KEY, 'baseline')
        assert result.allowed is True
        assert result.wait_seconds == 0
        assert result.next_attempt_number == 1

    def test_final_first_attempt_allowed(self):
        """First final attempt is always allowed."""
        result = check_assessment_eligibility(self.user, COURSE_KEY, 'final')
        assert result.allowed is True
        assert result.wait_seconds == 0

    def test_final_cooldown_after_three_attempts(self):
        """AC8: After 3 final attempts, cooldown applies."""
        for i in range(3):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user,
                course_key=COURSE_KEY,
                assessment_type='final',
                assessment_key='',
                attempt_number=i + 1,
                idempotency_key=uuid.uuid4(),
                correct_count=0,
                total_count=5,
                passed=False,
                question_results=[],
                submitted_at=timezone.now(),
            )
        result = check_assessment_eligibility(self.user, COURSE_KEY, 'final')
        assert result.allowed is False
        assert result.wait_seconds > 0

    def test_final_allowed_after_cooldown_expires(self):
        """After cooldown expires, the next attempt is allowed (not a permanent lockout)."""
        past = timezone.now() - timedelta(seconds=120)
        for i in range(3):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user,
                course_key=COURSE_KEY,
                assessment_type='final',
                assessment_key='',
                attempt_number=i + 1,
                idempotency_key=uuid.uuid4(),
                correct_count=0,
                total_count=5,
                passed=False,
                question_results=[],
                submitted_at=past,
            )
        result = check_assessment_eligibility(self.user, COURSE_KEY, 'final')
        assert result.allowed is True
        assert result.wait_seconds == 0


class RecordAssessmentLogicTest(TestCase):
    """Tests for assessment.record_assessment business logic."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='record_user', email='rec@test.com', password='pw'
        )

    def test_server_applies_pass_threshold(self):
        """AC7: Score below threshold → passed=False regardless of client intent."""
        attempt, passed = record_assessment(
            self.user, COURSE_KEY, 'final', score=3, total=5
        )
        assert passed is False
        assert attempt.passed is False

    def test_score_at_threshold_passes(self):
        """Score >= threshold → passed=True."""
        attempt, passed = record_assessment(
            self.user, COURSE_KEY, 'final', score=4, total=5
        )
        assert passed is True
        assert attempt.passed is True

    def test_baseline_passed_is_none(self):
        """AC9: Baseline passed is always None (informational)."""
        _, passed = record_assessment(
            self.user, COURSE_KEY, 'baseline', score=5, total=5
        )
        assert passed is None

    def test_invalid_score_raises_value_error(self):
        """AC10: score > total raises ValueError."""
        with self.assertRaises(ValueError):
            record_assessment(self.user, COURSE_KEY, 'final', score=6, total=5)

    def test_cooldown_raises_cooldown_active_error(self):
        """AC8: CooldownActiveError raised when in cooldown window."""
        for i in range(3):
            UberLearnAssessmentAttempt.objects.create(
                user=self.user,
                course_key=COURSE_KEY,
                assessment_type='final',
                assessment_key='',
                attempt_number=i + 1,
                idempotency_key=uuid.uuid4(),
                correct_count=0,
                total_count=5,
                passed=False,
                question_results=[],
                submitted_at=timezone.now(),
            )
        with self.assertRaises(CooldownActiveError):
            record_assessment(self.user, COURSE_KEY, 'final', score=2, total=5)

    def test_attempt_number_increments(self):
        """Attempt number increments on consecutive calls."""
        attempt1, _ = record_assessment(
            self.user, COURSE_KEY, 'baseline', score=3, total=5
        )
        attempt2, _ = record_assessment(
            self.user, COURSE_KEY, 'baseline', score=4, total=5
        )
        assert attempt1.attempt_number == 1
        assert attempt2.attempt_number == 2


# ---------------------------------------------------------------------------
# HTTP API tests (thin — exercise the view layer with mocked enrollment)
# ---------------------------------------------------------------------------

class ProgressViewTest(_EnrollmentMixin, TestCase):
    """Tests for GET /api/uber_learn/v1/progress/<course_key>."""

    def test_unauthenticated_returns_401(self):
        """AC12: Unauthenticated GET returns 401."""
        resp = APIClient().get(PROGRESS_URL)
        assert resp.status_code == 401

    def test_not_enrolled_returns_403(self):
        """AC1: Not-enrolled authenticated user gets 403."""
        client, _ = self._enrolled_client('notenc')
        with self._mock_enrolled(False):
            resp = client.get(PROGRESS_URL)
        assert resp.status_code == 403

    def test_enrolled_empty_course_returns_200(self):
        """AC2: Enrolled user with no activity gets 200 with zeroed progress."""
        client, _ = self._enrolled_client('enc2')
        with self._mock_enrolled(True):
            resp = client.get(PROGRESS_URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data['points']['earned'] == 0
        assert data['activities']['completed'] == 0
        assert 'badges' in data


class ActivityViewTest(_EnrollmentMixin, TestCase):
    """Tests for POST /api/uber_learn/v1/progress/<course_key>/activity."""

    def test_unauthenticated_returns_401(self):
        """AC12: Unauthenticated POST returns 401."""
        resp = APIClient().post(ACTIVITY_URL, {'activity_key': ACTIVITY_KEY})
        assert resp.status_code == 401

    def test_not_enrolled_returns_403(self):
        """AC6: Not-enrolled user gets 403."""
        client, _ = self._enrolled_client('notenc3')
        with self._mock_enrolled(False):
            resp = client.post(ACTIVITY_URL, {'activity_key': ACTIVITY_KEY})
        assert resp.status_code == 403

    def test_missing_activity_key_returns_400(self):
        """AC11: Missing activity_key field returns 400."""
        client, _ = self._enrolled_client('missing')
        with self._mock_enrolled(True):
            resp = client.post(ACTIVITY_URL, {})
        assert resp.status_code == 400

    def test_first_video_completion_earns_points(self):
        """AC3: First activity POST earns points."""
        client, _ = self._enrolled_client('actv')
        with self._mock_enrolled(True), \
             mock.patch('lms.djangoapps.uber_learn.views.record_activity', return_value=(10, True)):
            resp = client.post(ACTIVITY_URL, {'activity_key': ACTIVITY_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data['points_awarded_now'] == 10
        assert data['newly_completed'] is True

    def test_second_video_completion_earns_zero(self):
        """AC5: Second POST earns 0 points (idempotent)."""
        client, _ = self._enrolled_client('actv2')
        with self._mock_enrolled(True), \
             mock.patch('lms.djangoapps.uber_learn.views.record_activity', return_value=(0, False)):
            resp = client.post(ACTIVITY_URL, {'activity_key': ACTIVITY_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data['points_awarded_now'] == 0
        assert data['newly_completed'] is False


class AssessmentViewTest(_EnrollmentMixin, TestCase):
    """Tests for POST /api/uber_learn/v1/progress/<course_key>/assessment."""

    def test_unauthenticated_returns_401(self):
        """AC12: Unauthenticated POST returns 401."""
        resp = APIClient().post(ASSESSMENT_URL, {
            'assessment_type': 'final',
            'idempotency_key': str(uuid.uuid4()),
        })
        assert resp.status_code == 401

    def test_not_enrolled_returns_403(self):
        """AC6/AC7: Not-enrolled user gets 403."""
        client, _ = self._enrolled_client('assnotenc')
        with self._mock_enrolled(False):
            resp = client.post(ASSESSMENT_URL, {
                'assessment_type': 'final',
                'idempotency_key': str(uuid.uuid4()),
            })
        assert resp.status_code == 403

    def test_missing_assessment_type_returns_400(self):
        """AC11: Missing assessment_type returns 400."""
        client, _ = self._enrolled_client('assmiss')
        with self._mock_enrolled(True):
            resp = client.post(ASSESSMENT_URL, {
                'idempotency_key': str(uuid.uuid4()),
            })
        assert resp.status_code == 400

    def test_invalid_assessment_type_returns_400(self):
        """AC11: Invalid assessment_type returns 400."""
        client, _ = self._enrolled_client('assinv')
        with self._mock_enrolled(True):
            resp = client.post(ASSESSMENT_URL, {
                'assessment_type': 'not_a_type',
                'idempotency_key': str(uuid.uuid4()),
            })
        assert resp.status_code == 400

    def test_cooldown_returns_429(self):
        """AC8: Cooldown returns 429 with retry_after_seconds."""
        client, _ = self._enrolled_client('asscool')
        with self._mock_enrolled(True), \
             mock.patch(
                 'lms.djangoapps.uber_learn.views.score_and_record_assessment',
                 side_effect=CooldownActiveError(45),
             ):
            resp = client.post(ASSESSMENT_URL, {
                'assessment_type': 'final',
                'idempotency_key': str(uuid.uuid4()),
            })
        assert resp.status_code == 429
        assert resp.json()['retry_after_seconds'] == 45
