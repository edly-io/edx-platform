"""
Course structure utilities for Uber Learn: assessment config, question discovery,
and server-authoritative scoring via StudentModule.

Design reference: solution-design sections E.2 and F.1.

All platform imports are lazy (inside functions) to avoid coupling at
app-registry time, matching the pattern used in scoring.py and views.py.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from django.utils.dateparse import parse_datetime

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

log = logging.getLogger(__name__)

COURSE_SETTINGS_KEY = 'uber_learn_assessments'
VALID_ASSESSMENT_TYPES = frozenset({'baseline', 'final', 'retention'})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AssessmentNotConfiguredError(Exception):
    """Raised when the assessment type is absent from other_course_settings,
    or the configured key has no scorable descendants."""


class AssessmentIncompleteError(Exception):
    """Raised when one or more questions have no fresh graded submission
    (design doc E.2 — doesn't consume an attempt)."""

    def __init__(self, missing_keys: list) -> None:
        self.missing_keys = [str(k) for k in missing_keys]
        super().__init__(
            f"Assessment incomplete: {len(missing_keys)} question(s) have no fresh grade"
        )


# ---------------------------------------------------------------------------
# Config reader
# ---------------------------------------------------------------------------

def read_assessment_config(course_id: str) -> dict[str, str]:
    """
    Read other_course_settings[COURSE_SETTINGS_KEY] for the given course.

    Returns a dict mapping assessment type → usage-key string.
    Accepts both plain-string values and dict values (with 'usage_key' or
    'sequence_key' keys) for forward-compatibility with the full manifest design.

    Returns an empty dict on any error so callers can raise a clean 404.
    """
    try:
        from opaque_keys.edx.keys import CourseKey  # noqa: PLC0415
        from xmodule.modulestore.django import modulestore  # noqa: PLC0415

        course_key_obj = CourseKey.from_string(course_id)
        course = modulestore().get_course(course_key_obj, depth=0)
        if course is None:
            log.error("uber_learn: course not found: %s", course_id)
            return {}

        raw = (course.other_course_settings or {}).get(COURSE_SETTINGS_KEY, {})
        if not isinstance(raw, dict):
            log.error(
                "uber_learn: %s is not a dict in course %s — got %r",
                COURSE_SETTINGS_KEY, course_id, type(raw).__name__,
            )
            return {}

        config: dict[str, str] = {}
        for a_type, value in raw.items():
            if a_type not in VALID_ASSESSMENT_TYPES:
                log.warning(
                    "uber_learn: unknown assessment type %r in course %s, ignoring",
                    a_type, course_id,
                )
                continue
            if isinstance(value, str):
                config[a_type] = value
            elif isinstance(value, dict):
                key = value.get('usage_key') or value.get('sequence_key')
                if key:
                    config[a_type] = str(key)
                else:
                    log.error(
                        "uber_learn: assessment config for %r in %s is a dict but has no "
                        "'usage_key' or 'sequence_key'",
                        a_type, course_id,
                    )
            else:
                log.error(
                    "uber_learn: unexpected config shape for %r in %s: %r",
                    a_type, course_id, type(value).__name__,
                )
        return config

    except Exception:  # noqa: BLE001
        log.exception("uber_learn: failed to read assessment config for course %s", course_id)
        return {}


# ---------------------------------------------------------------------------
# Block-structure walk: find scorable descendants
# ---------------------------------------------------------------------------

def get_question_keys(
    user: 'AbstractUser',
    course_id: str,
    assessment_key_str: str,
) -> list:
    """
    Walk the course block structure rooted at assessment_key_str to collect
    all has_score=True descendant usage keys, in outline order.

    The assessment key may be a sequential (each unit is one question) or a
    vertical (all questions on one page) — both are handled by the recursive walk.

    Returns an empty list on any block-structure error; callers raise the
    appropriate exception.
    """
    try:
        from lms.djangoapps.course_blocks.api import get_course_blocks  # noqa: PLC0415
        from opaque_keys.edx.keys import CourseKey, UsageKey  # noqa: PLC0415
        from xmodule.modulestore.django import modulestore  # noqa: PLC0415

        course_key_obj = CourseKey.from_string(course_id)
        assessment_key = UsageKey.from_string(assessment_key_str).map_into_course(course_key_obj)

        course = modulestore().get_course(course_key_obj, depth=0)
        if course is None:
            return []

        blocks = get_course_blocks(user, course.location)
        if assessment_key not in blocks:
            log.warning(
                "uber_learn: assessment key %s not found in block structure for course %s",
                assessment_key_str, course_id,
            )
            return []

        question_keys: list = []
        _collect_scorable_descendants(blocks, assessment_key, question_keys)
        return question_keys

    except Exception:  # noqa: BLE001
        log.exception(
            "uber_learn: failed to get question keys for %s in %s",
            assessment_key_str, course_id,
        )
        return []


def _collect_scorable_descendants(blocks, block_key: object, result: list) -> None:
    """Recurse depth-first, collecting has_score=True blocks in outline order."""
    if blocks.get_xblock_field(block_key, 'has_score', False):
        result.append(block_key)
        return  # treat as leaf — don't recurse into scored blocks
    for child_key in blocks.get_children(block_key):
        _collect_scorable_descendants(blocks, child_key, result)


# ---------------------------------------------------------------------------
# Main entry point: server-authoritative assessment scoring
# ---------------------------------------------------------------------------

def score_assessment(
    user: 'AbstractUser',
    course_id: str,
    a_type: str,
) -> dict:
    """
    Compute assessment score from StudentModule grades (design doc E.2).

    The client sends only {assessment_type, idempotency_key}; all scoring is
    server-derived here.

    Returns:
        {
            'correct': int,           # questions answered correctly
            'total': int,             # total questions
            'question_results': [     # per-question detail
                {'usage_key': str, 'correct': bool}, ...
            ],
            'assessment_key': str,    # the block key for this assessment type
        }

    Raises:
        AssessmentNotConfiguredError  — type absent from other_course_settings,
                                       or no scorable questions found under it
        AssessmentIncompleteError     — one or more questions have no fresh grade
    """
    config = read_assessment_config(course_id)
    if a_type not in config:
        raise AssessmentNotConfiguredError(
            f"Assessment type {a_type!r} is not configured for course {course_id}. "
            f"Set other_course_settings['{COURSE_SETTINGS_KEY}']['{a_type}'] in Studio."
        )

    assessment_key_str = config[a_type]
    question_keys = get_question_keys(user, course_id, assessment_key_str)
    if not question_keys:
        raise AssessmentNotConfiguredError(
            f"No scorable questions found under {assessment_key_str} "
            f"(course {course_id}, type {a_type!r}). "
            f"Ensure the assessment block has has_score=True descendants."
        )

    # Freshness gate (design doc E.2): each question must have been submitted
    # AFTER the previous attempt of this type was recorded.
    from lms.djangoapps.uber_learn.models import UberLearnAssessmentAttempt  # noqa: PLC0415

    prev = (
        UberLearnAssessmentAttempt.objects.filter(
            user=user, course_key=course_id, assessment_type=a_type
        )
        .order_by('-submitted_at')
        .first()
    )
    since = prev.submitted_at if prev else None

    correct, total, question_results = _read_scores_from_student_modules(
        user=user,
        course_id=course_id,
        question_keys=question_keys,
        since=since,
    )

    return {
        'correct': correct,
        'total': total,
        'question_results': question_results,
        'assessment_key': assessment_key_str,
    }


# ---------------------------------------------------------------------------
# StudentModule query
# ---------------------------------------------------------------------------

def _read_scores_from_student_modules(
    user: 'AbstractUser',
    course_id: str,
    question_keys: list,
    since,
) -> tuple[int, int, list]:
    """
    Query StudentModule for each question key and return (correct, total, results).

    Raises AssessmentIncompleteError if any question is missing or stale.
    """
    from lms.djangoapps.courseware.models import StudentModule  # noqa: PLC0415
    from opaque_keys.edx.keys import CourseKey  # noqa: PLC0415

    course_key_obj = CourseKey.from_string(course_id)

    rows = StudentModule.objects.filter(
        student_id=user.id,
        course_id=course_key_obj,
        module_state_key__in=question_keys,
    ).values('module_state_key', 'grade', 'max_grade', 'state', 'modified')

    # Index rows by their usage key for O(1) lookup.
    by_key = {row['module_state_key']: row for row in rows}

    missing: list = []
    results: list[dict] = []

    for q_key in question_keys:
        row = by_key.get(q_key)

        if row is None or row['max_grade'] in (None, 0) or row['grade'] is None:
            missing.append(q_key)
            continue

        # Parse submission time from CAPA state JSON if available; fall back to
        # the StudentModule.modified timestamp (DnD / Sortable don't store it).
        submitted_at = _parse_submission_time(row.get('state'), row['modified'])

        # Stale answer from a previous attempt — must re-answer.
        if since is not None and submitted_at is not None and submitted_at <= since:
            missing.append(q_key)
            continue

        is_correct = row['grade'] >= row['max_grade']
        results.append({'usage_key': str(q_key), 'correct': is_correct})

    if missing:
        raise AssessmentIncompleteError(missing)

    correct = sum(1 for r in results if r['correct'])
    total = len(results)
    return correct, total, results


def _parse_submission_time(state_json: str | None, fallback):
    """
    Parse last_submission_time from CAPA state JSON.

    CAPA stores the submission time as an ISO-8601 string under
    state['last_submission_time']. DnD and Sortable don't store it, so we
    fall back to StudentModule.modified (which is close enough for the
    freshness check).
    """
    if state_json:
        try:
            state = json.loads(state_json)
            ts_str = state.get('last_submission_time')
            if ts_str:
                dt = parse_datetime(ts_str)
                if dt is not None:
                    return dt
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    return fallback
