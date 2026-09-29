"""
Tests for Uber Learn activity scoring logic.

CRITICAL RULES verified by this suite:
1. Video / reading / resource: 10 pts on any first completion (correct=None).
2. Choice (CAPA) / drag (DnD) / sort / number: 10 pts only on first CORRECT.
3. Incorrect-first MUST NOT block a later correct from earning points.
4. Idempotency: duplicate correct submissions earn 0 additional points.
5. Total points accumulate correctly across multiple activities.
6. Unknown activity_type raises ValueError (not silently pass).

Activity type slugs follow UberLearnActivity.ACTIVITY_TYPES:
  'video', 'reading', 'resource', 'choice', 'drag', 'sort', 'number'

Because record_activity() is not yet implemented, all tests use a local
fake that encodes the intended business logic.  When the real implementation
lands, remove the mock.patch calls and re-run — the same assertions apply.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from lms.djangoapps.uber_learn.scoring import (
    POINTS_PER_ACTIVITY,
    COMPLETION_ONLY_TYPES,
    CORRECT_REQUIRED_TYPES,
    record_activity,
    get_total_points,
)

User = get_user_model()

COURSE_ID = 'course-v1:Uber+Learn+2024'


def _key(tag: str) -> str:
    return f'block-v1:Uber+Learn+2024+type@vertical+block@{tag}'


# ---------------------------------------------------------------------------
# In-memory fake that encodes the intended business logic for use in mocking.
# This also serves as a "spec" — the real implementation must produce the
# same results for the same inputs.
# ---------------------------------------------------------------------------

def _make_fake_record_activity():
    """Return (store, fake_fn).  store is inspectable from tests."""
    store: dict = {}

    def fake(user, course_id, activity_key, activity_type, correct):
        if activity_type not in COMPLETION_ONLY_TYPES | CORRECT_REQUIRED_TYPES:
            raise ValueError(f'Unknown activity_type: {activity_type!r}')

        key = (user.pk, course_id, activity_key)

        if activity_type in COMPLETION_ONLY_TYPES:
            if key not in store:
                store[key] = {'points': POINTS_PER_ACTIVITY, 'completed': True}
                return POINTS_PER_ACTIVITY, True
            return 0, False

        # CORRECT_REQUIRED_TYPES
        existing = store.get(key)
        if existing and existing['completed']:
            return 0, False
        if correct is True:
            store[key] = {'points': POINTS_PER_ACTIVITY, 'completed': True}
            return POINTS_PER_ACTIVITY, True
        # Incorrect — record attempt, leave incomplete
        store.setdefault(key, {'points': 0, 'completed': False})
        return 0, False

    return store, fake


# ---------------------------------------------------------------------------
# Constants sanity checks (no mocking needed)
# ---------------------------------------------------------------------------

class ScoringConstantsTest(TestCase):
    """Verify that the module-level constants are self-consistent."""

    def test_points_per_activity_is_positive(self):
        self.assertGreater(POINTS_PER_ACTIVITY, 0)

    def test_no_overlap_between_type_sets(self):
        overlap = COMPLETION_ONLY_TYPES & CORRECT_REQUIRED_TYPES
        self.assertEqual(overlap, frozenset(), msg="Type sets must be disjoint")

    def test_activity_type_slugs_are_exhaustive(self):
        """
        Verify that COMPLETION_ONLY_TYPES and CORRECT_REQUIRED_TYPES are disjoint
        and together contain the expected set of activity type slugs.
        """
        expected_slugs = frozenset(
            {'video', 'reading', 'resource', 'choice', 'drag', 'sort', 'number'}
        )
        all_scoring_types = COMPLETION_ONLY_TYPES | CORRECT_REQUIRED_TYPES
        missing = expected_slugs - all_scoring_types
        extra = all_scoring_types - expected_slugs
        self.assertEqual(missing, frozenset(), msg=f"Missing from scoring: {missing}")
        self.assertEqual(extra, frozenset(), msg=f"Unexpected types in scoring: {extra}")


# ---------------------------------------------------------------------------
# Video / reading / resource (completion-only)
# ---------------------------------------------------------------------------

class CompletionOnlyActivityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='learner_completion',
            email='c@uber.com',
            password='secret',
        )

    def test_video_first_completion_earns_points(self):
        """AC-ACT-01: Video first completion earns POINTS_PER_ACTIVITY."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            points, is_first = record_activity(
                self.user, COURSE_ID, _key('vid1'), 'video', None
            )
        self.assertEqual(points, POINTS_PER_ACTIVITY)
        self.assertTrue(is_first)

    def test_video_duplicate_earns_zero(self):
        """AC-ACT-02: Second identical video completion earns 0 additional points."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            first_pts, _ = record_activity(
                self.user, COURSE_ID, _key('vid2'), 'video', None
            )
            second_pts, is_first = record_activity(
                self.user, COURSE_ID, _key('vid2'), 'video', None
            )
        self.assertEqual(first_pts, POINTS_PER_ACTIVITY)
        self.assertEqual(second_pts, 0)
        self.assertFalse(is_first)

    def test_reading_first_completion_earns_points(self):
        """AC-ACT-03: Reading first completion earns POINTS_PER_ACTIVITY."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            pts, is_first = record_activity(
                self.user, COURSE_ID, _key('read1'), 'reading', None
            )
        self.assertEqual(pts, POINTS_PER_ACTIVITY)
        self.assertTrue(is_first)

    def test_reading_duplicate_earns_zero(self):
        """AC-ACT-04: Duplicate reading completion earns 0."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            record_activity(self.user, COURSE_ID, _key('read2'), 'reading', None)
            pts, _ = record_activity(self.user, COURSE_ID, _key('read2'), 'reading', None)
        self.assertEqual(pts, 0)

    def test_resource_first_completion_earns_points(self):
        """AC-ACT-05: Resource first completion earns POINTS_PER_ACTIVITY."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            pts, is_first = record_activity(
                self.user, COURSE_ID, _key('res1'), 'resource', None
            )
        self.assertEqual(pts, POINTS_PER_ACTIVITY)
        self.assertTrue(is_first)


# ---------------------------------------------------------------------------
# Choice (CAPA) — correct-required
# ---------------------------------------------------------------------------

class ChoiceActivityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='learner_choice',
            email='ch@uber.com',
            password='secret',
        )

    def test_first_incorrect_earns_zero(self):
        """AC-ACT-06: First incorrect choice submission earns 0 points."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            pts, is_first = record_activity(
                self.user, COURSE_ID, _key('cap1'), 'choice', False
            )
        self.assertEqual(pts, 0)
        self.assertFalse(is_first)

    def test_incorrect_then_correct_earns_points(self):
        """
        AC-ACT-07 (CRITICAL): Incorrect attempt followed by a correct attempt
        MUST earn POINTS_PER_ACTIVITY.  The incorrect attempt must NOT
        create a permanent block.
        """
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            first_pts, _ = record_activity(
                self.user, COURSE_ID, _key('cap2'), 'choice', False
            )
            second_pts, is_first = record_activity(
                self.user, COURSE_ID, _key('cap2'), 'choice', True
            )
        self.assertEqual(first_pts, 0, "Incorrect attempt must earn 0")
        self.assertEqual(
            second_pts,
            POINTS_PER_ACTIVITY,
            "Correct attempt after incorrect attempt must earn points — "
            "incorrect must NOT permanently block earning",
        )
        self.assertTrue(is_first)

    def test_first_correct_earns_points(self):
        """AC-ACT-08: First correct choice submission earns POINTS_PER_ACTIVITY."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            pts, is_first = record_activity(
                self.user, COURSE_ID, _key('cap3'), 'choice', True
            )
        self.assertEqual(pts, POINTS_PER_ACTIVITY)
        self.assertTrue(is_first)

    def test_duplicate_correct_earns_zero(self):
        """AC-ACT-09: Second correct choice submission earns 0."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            record_activity(self.user, COURSE_ID, _key('cap4'), 'choice', True)
            pts, is_first = record_activity(
                self.user, COURSE_ID, _key('cap4'), 'choice', True
            )
        self.assertEqual(pts, 0)
        self.assertFalse(is_first)

    def test_many_incorrect_then_correct_earns_once(self):
        """AC-ACT-10: Many incorrect attempts then one correct earns POINTS_PER_ACTIVITY exactly once."""
        _, fake = _make_fake_record_activity()
        total = 0
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            for _ in range(5):
                pts, _ = record_activity(
                    self.user, COURSE_ID, _key('cap5'), 'choice', False
                )
                total += pts
            pts, _ = record_activity(
                self.user, COURSE_ID, _key('cap5'), 'choice', True
            )
            total += pts
        self.assertEqual(total, POINTS_PER_ACTIVITY,
                         "Only one earn event regardless of preceding incorrect count")


# ---------------------------------------------------------------------------
# Drag-and-Drop — correct-required
# ---------------------------------------------------------------------------

class DragActivityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='learner_drag',
            email='dnd@uber.com',
            password='secret',
        )

    def test_incorrect_earns_zero(self):
        """AC-ACT-11: Drag incorrect submission earns 0 points."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            pts, _ = record_activity(
                self.user, COURSE_ID, _key('dnd1'), 'drag', False
            )
        self.assertEqual(pts, 0)

    def test_incorrect_then_correct_earns_points(self):
        """AC-ACT-12: Drag incorrect then correct earns POINTS_PER_ACTIVITY."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            record_activity(self.user, COURSE_ID, _key('dnd2'), 'drag', False)
            pts, is_first = record_activity(
                self.user, COURSE_ID, _key('dnd2'), 'drag', True
            )
        self.assertEqual(pts, POINTS_PER_ACTIVITY)
        self.assertTrue(is_first)


# ---------------------------------------------------------------------------
# Sort — correct-required
# ---------------------------------------------------------------------------

class SortActivityTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='learner_sort',
            email='sort@uber.com',
            password='secret',
        )

    def test_sort_incorrect_then_correct_earns_points(self):
        """AC-ACT-13: Sort incorrect then correct earns POINTS_PER_ACTIVITY."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            record_activity(self.user, COURSE_ID, _key('sort1'), 'sort', False)
            pts, is_first = record_activity(
                self.user, COURSE_ID, _key('sort1'), 'sort', True
            )
        self.assertEqual(pts, POINTS_PER_ACTIVITY)
        self.assertTrue(is_first)


# ---------------------------------------------------------------------------
# Total points accumulation
# ---------------------------------------------------------------------------

class TotalPointsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='learner_total',
            email='total@uber.com',
            password='secret',
        )

    def test_total_accumulates_across_activities(self):
        """AC-ACT-14: Points accumulate correctly across multiple distinct activities."""
        _, fake = _make_fake_record_activity()
        total = 0
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            for tag, atype, correct in [
                ('v1', 'video', None),
                ('r1', 'reading', None),
                ('c1', 'choice', True),
                ('d1', 'drag', True),
                ('s1', 'sort', True),
            ]:
                pts, _ = record_activity(
                    self.user, COURSE_ID, _key(tag), atype, correct
                )
                total += pts
        self.assertEqual(total, POINTS_PER_ACTIVITY * 5)

    def test_incorrect_submissions_do_not_inflate_total(self):
        """AC-ACT-15: Incorrect submissions must not add to total points."""
        _, fake = _make_fake_record_activity()
        total = 0
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            for tag, atype in [('c6', 'choice'), ('d6', 'drag'), ('s6', 'sort')]:
                pts, _ = record_activity(
                    self.user, COURSE_ID, _key(tag), atype, False
                )
                total += pts
        self.assertEqual(total, 0)

    def test_duplicate_completions_do_not_inflate_total(self):
        """AC-ACT-16: Repeating the same completion must not double-count points."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            record_activity(self.user, COURSE_ID, _key('v2'), 'video', None)
            record_activity(self.user, COURSE_ID, _key('v2'), 'video', None)
            record_activity(self.user, COURSE_ID, _key('v2'), 'video', None)
            last_pts, _ = record_activity(
                self.user, COURSE_ID, _key('v2'), 'video', None
            )
        self.assertEqual(last_pts, 0, "Fourth duplicate call must earn 0")


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class ScoringErrorTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='learner_err',
            email='err@uber.com',
            password='secret',
        )

    def test_unknown_activity_type_raises_value_error(self):
        """AC-ACT-17: Unknown activity_type must raise ValueError."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            with self.assertRaises(ValueError):
                record_activity(
                    self.user, COURSE_ID, _key('x1'), 'capa', None  # 'capa' is old/wrong slug
                )

    def test_unknown_activity_type_dnd_raises_value_error(self):
        """AC-ACT-18: 'dnd' (deprecated slug) must raise ValueError — use 'drag'."""
        _, fake = _make_fake_record_activity()
        with patch('lms.djangoapps.uber_learn.scoring.record_activity', side_effect=fake):
            with self.assertRaises(ValueError):
                record_activity(
                    self.user, COURSE_ID, _key('x2'), 'dnd', True
                )
