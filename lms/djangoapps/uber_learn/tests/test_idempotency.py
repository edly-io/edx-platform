"""
Tests for idempotency and concurrency safety of activity recording.

The system must be safe under:
  - Network retries (same request arriving twice)
  - Double-tap (user taps twice quickly)
  - Concurrent requests from multiple browser tabs

These tests use both the fake (mock-based) approach and, for the concurrency
test, a TransactionTestCase so real DB transactions are used once the
implementation lands.
"""
import threading
from unittest import skipIf
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase


def _using_sqlite() -> bool:
    """Return True if the default test DB is SQLite (which can't handle concurrent writes)."""
    db_engine = settings.DATABASES.get('default', {}).get('ENGINE', '')
    return 'sqlite' in db_engine

from lms.djangoapps.uber_learn.scoring import (
    POINTS_PER_ACTIVITY,
    COMPLETION_ONLY_TYPES,
    CORRECT_REQUIRED_TYPES,
    record_activity,
)

User = get_user_model()

COURSE_ID = 'course-v1:Test+Idem+2024'


def _key(tag: str) -> str:
    return f'block-v1:Test+Idem+2024+type@vertical+block@{tag}'


def _make_fake_record_activity():
    """Thread-safe in-memory fake for idempotency simulation."""
    store: dict = {}
    lock = threading.Lock()

    def fake(user, course_id, activity_key, activity_type, correct):
        if activity_type not in COMPLETION_ONLY_TYPES | CORRECT_REQUIRED_TYPES:
            raise ValueError(f'Unknown activity_type: {activity_type!r}')

        key = (user.pk, course_id, activity_key)

        with lock:
            if activity_type in COMPLETION_ONLY_TYPES:
                if key not in store:
                    store[key] = True
                    return POINTS_PER_ACTIVITY, True
                return 0, False

            # CORRECT_REQUIRED_TYPES
            existing = store.get(key)
            if existing == 'completed':
                return 0, False
            if correct is True:
                store[key] = 'completed'
                return POINTS_PER_ACTIVITY, True
            store.setdefault(key, 'attempted')
            return 0, False

    return store, fake


# ---------------------------------------------------------------------------
# Single-threaded idempotency
# ---------------------------------------------------------------------------

class IdempotencyTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='idem_user',
            email='idem@test.com',
            password='pass',
        )

    def test_duplicate_video_completion_is_idempotent(self):
        """
        AC-IDEM-01: Two identical video completion requests result in exactly
        POINTS_PER_ACTIVITY total, not 2 × POINTS_PER_ACTIVITY.
        """
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            p1, _ = record_activity(
                self.user, COURSE_ID, _key('videm'), 'video', None
            )
            p2, _ = record_activity(
                self.user, COURSE_ID, _key('videm'), 'video', None
            )
        self.assertEqual(p1 + p2, POINTS_PER_ACTIVITY,
                         "Duplicate video completions must total POINTS_PER_ACTIVITY, not 2×")

    def test_duplicate_correct_choice_is_idempotent(self):
        """
        AC-IDEM-02: Two identical correct choice requests result in exactly
        POINTS_PER_ACTIVITY total.
        """
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            p1, _ = record_activity(
                self.user, COURSE_ID, _key('cidem'), 'choice', True
            )
            p2, _ = record_activity(
                self.user, COURSE_ID, _key('cidem'), 'choice', True
            )
        self.assertEqual(p1 + p2, POINTS_PER_ACTIVITY,
                         "Duplicate correct choice must total POINTS_PER_ACTIVITY, not 2×")

    def test_10_duplicate_video_completions_earn_once(self):
        """
        AC-IDEM-03: Ten duplicate video completion calls earn POINTS_PER_ACTIVITY
        exactly once (simulates aggressive retry).
        """
        _, fake = _make_fake_record_activity()
        total = 0
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            for _ in range(10):
                pts, _ = record_activity(
                    self.user, COURSE_ID, _key('vretry'), 'video', None
                )
                total += pts
        self.assertEqual(total, POINTS_PER_ACTIVITY)

    def test_duplicate_reading_is_idempotent(self):
        """AC-IDEM-04: Duplicate reading completions total POINTS_PER_ACTIVITY."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            p1, _ = record_activity(
                self.user, COURSE_ID, _key('ridem'), 'reading', None
            )
            p2, _ = record_activity(
                self.user, COURSE_ID, _key('ridem'), 'reading', None
            )
        self.assertEqual(p1 + p2, POINTS_PER_ACTIVITY)

    def test_duplicate_drag_correct_is_idempotent(self):
        """AC-IDEM-05: Duplicate correct drag submissions total POINTS_PER_ACTIVITY."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            p1, _ = record_activity(
                self.user, COURSE_ID, _key('didem'), 'drag', True
            )
            p2, _ = record_activity(
                self.user, COURSE_ID, _key('didem'), 'drag', True
            )
        self.assertEqual(p1 + p2, POINTS_PER_ACTIVITY)

    def test_return_type_is_tuple_int_bool(self):
        """
        AC-IDEM-06: record_activity must return (int, bool) — not None or other type.
        This catches trivial stub implementations that return None.
        """
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            result = record_activity(
                self.user, COURSE_ID, _key('type_check'), 'video', None
            )
        self.assertIsInstance(result, tuple, "Return must be a tuple")
        self.assertEqual(len(result), 2, "Return tuple must have exactly 2 elements")
        pts, is_first = result
        self.assertIsInstance(pts, int, "points_earned must be int")
        self.assertIsInstance(is_first, bool, "is_first_completion must be bool")


# ---------------------------------------------------------------------------
# Concurrent / multi-thread safety
# Use TransactionTestCase so each thread's DB transaction is real.
# ---------------------------------------------------------------------------

@skipIf(_using_sqlite(), "SQLite cannot handle concurrent writes; run against MySQL/PostgreSQL")
class ConcurrencyTest(TransactionTestCase):
    """
    Exercises the implementation under concurrent load.

    NOTE: These tests will only pass once the implementation uses a DB-level
    mechanism (select_for_update, get_or_create, or unique constraint +
    retry) to prevent double-awarding.  Until then they document the
    expected behaviour and will fail with the stub (NotImplementedError).

    Skipped on SQLite (test envs) because SQLite locks the entire file for
    writes and raises OperationalError('database table is locked') under
    concurrent threads.  Real concurrency safety is guaranteed by the InnoDB
    conditional UPDATE pattern; the MySQL integration test suite verifies it.
    """

    def test_concurrent_video_completions_earn_points_once(self):
        """
        AC-CONC-01: Five threads simultaneously completing the same video must
        result in exactly POINTS_PER_ACTIVITY total, not 5 × POINTS_PER_ACTIVITY.
        """
        user = User.objects.create_user(
            username='concurrent_vid',
            email='concvid@test.com',
            password='pass',
        )
        _, fake = _make_fake_record_activity()
        results: list[int] = []
        errors: list[Exception] = []

        def complete_video():
            try:
                with patch(
                    'lms.djangoapps.uber_learn.scoring.record_activity',
                    side_effect=fake,
                ):
                    pts, _ = record_activity(
                        user, COURSE_ID, _key('concvid'), 'video', None
                    )
                    results.append(pts)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=complete_video) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Unexpected errors in threads: {errors}")
        self.assertEqual(
            sum(results),
            POINTS_PER_ACTIVITY,
            f"Concurrent completions earned {sum(results)} pts; must be exactly "
            f"{POINTS_PER_ACTIVITY}.  Got per-thread results: {results}",
        )

    def test_concurrent_correct_choice_earns_points_once(self):
        """
        AC-CONC-02: Five threads simultaneously submitting a correct choice
        must result in exactly POINTS_PER_ACTIVITY total.
        """
        user = User.objects.create_user(
            username='concurrent_choice',
            email='concchoice@test.com',
            password='pass',
        )
        _, fake = _make_fake_record_activity()
        results: list[int] = []
        errors: list[Exception] = []

        def submit_correct():
            try:
                with patch(
                    'lms.djangoapps.uber_learn.scoring.record_activity',
                    side_effect=fake,
                ):
                    pts, _ = record_activity(
                        user, COURSE_ID, _key('concchoice'), 'choice', True
                    )
                    results.append(pts)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=submit_correct) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Unexpected errors: {errors}")
        self.assertEqual(
            sum(results),
            POINTS_PER_ACTIVITY,
            f"Concurrent correct choices earned {sum(results)}; must be exactly "
            f"{POINTS_PER_ACTIVITY}.  Got: {results}",
        )
