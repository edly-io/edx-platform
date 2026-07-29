"""
Export one Edly tenant's off-boarding data as human-readable CSVs, built by
calling the LMS's own per-course instructor-report generators directly.

This supersedes the earlier `export_learner_data.py` JSON-restore-bundle
command (removed from this branch). A review of that JSON bundle
(`pullrequestreview-4805493785`) argued the actual off-boarding need is a
CSV a non-technical operator can open, not a machine-shaped restore
bundle -- this command is that redirect, and the JSON command's
tenant/user/course-resolution patterns (referenced throughout this file
and its tests) were carried forward from it before it was deleted.

Execution mechanism: direct in-process calls, not real Celery tasks
=====================================================================
`CourseGradeReport.generate`, `ProblemResponses.generate`,
`upload_students_csv`, `upload_may_enroll_csv`, and `upload_ora2_data`
(`lms/djangoapps/instructor_task/tasks_helper/{grades,enrollments,misc}.py`)
are the exact functions the LMS's own "Instructor > Data Download" UI
submits as Celery tasks. This command calls them directly, in-process,
instead of submitting real Celery tasks -- avoiding shared-queue contention
with live student-facing work and the stuck-task-reservation risk a real
submission would carry if this command's run were interrupted (this
codebase ships a `fail_old_tasks` cleanup command precisely because that
happens to real InstructorTask rows).

This works because every one of these functions eventually calls
`TaskProgress.update_task_state` (`tasks_helper/runner.py`), which calls
`_get_current_task().update_state(...)`. `_get_current_task()`
(`runner.py`) is a one-line wrapper around `celery.current_task` whose own
docstring says it exists because "it doesn't seem to work to mock
current_task directly" -- a deliberately-provided test seam, not an
undocumented hack, and it's exactly what this codebase's own
`test_tasks_helper.py` already patches to call these functions outside
Celery. `_celery_free_context` below does the same thing.

Output capture: `upload_csv_to_report_store` is imported as a separate name
binding into three different modules (`grades.py`, `enrollments.py`,
`misc.py`), so `_capture_csv_uploads` patches all three individually rather
than the shared `tasks_helper/utils.py` original -- patching only the
original would leave these three modules still calling the *real*,
unpatched function via their own imported name. Capturing in-process this
way also means the file-correlation problem a real Celery submission would
face (`ReportStore` has no task-id-keyed lookup, only "most recent file in
this course's folder") never arises: we get exactly the rows this run
produced, no storage round-trip, no race window.

A regression test (`test_export_tenant_reports_csv.py`) asserts these exact
patch targets (`runner._get_current_task`, and each module's
`upload_csv_to_report_store` binding) still exist, so a future upstream
refactor of `tasks_helper/` fails loudly there instead of silently breaking
this command.

Per-function call signatures (confirmed by reading each -- one real gotcha)
=============================================================================
- `CourseGradeReport.generate(None, None, course_id, {}, 'grades')` --
  `_task_input` unused.
- `ProblemResponses.generate(None, None, course_id, task_input, 'responses')`
  -- requires `task_input['problem_locations']` (a *string*: `generate`
  calls `.split(',')` on it) and `task_input['user_id']`. This command
  passes the course's own root usage key
  (`str(modulestore().make_course_usage_key(course_id))`) so the whole
  course tree is walked rather than one problem -- confirmed against real
  source, not inferred: `test_tasks_helper.py`'s own
  `TestProblemResponsesReport.test_success` passes
  `{'problem_locations': str(self.course.location), 'user_id': ...}` into
  this exact function, and `self.course.location` is the course's root
  usage key. `MAX_PROBLEM_RESPONSES_COUNT` (a hard 5000-response-per-course
  cap by default) is left untouched unless an operator explicitly opts in
  via `--max-problem-responses` (an integer, or the literal `unlimited`) --
  see `_override_max_problem_responses_limit` -- with a loud log line, then
  restored. This is opt-in, not automatic: lifting the only bound on this
  report's memory footprint just because problem_responses was requested
  is a deliberate operator decision, not a default.
- `upload_students_csv(None, None, course_id, feature_list, 'features')` --
  **`task_input` here is the bare feature list itself**, not a dict
  (confirmed from `instructor_task/api.py`'s
  `submit_calculate_students_features_csv`, which passes `task_input =
  features` verbatim -- not the test file, which isn't ground truth for
  this signature quirk).
- `upload_may_enroll_csv(None, None, course_id, {'features': feature_list},
  'may_enroll')` -- **`task_input` is a dict** here, `.get('features')`.
- `upload_ora2_data(None, None, course_id, {}, 'ora2')` -- `_task_input`
  unused. On internal failure it returns the plain string `'failed'`
  (`UPDATE_STATUS_FAILED`) instead of raising -- this command branches on
  that return value, not a dict/exception.

Two structural scoping fixes (both confirmed via `export_learner_data.py`'s
own docstring, which already hit and fixed these once for the JSON export)
=============================================================================
1. Tenant membership (`EdlyMultiSiteAccess`), not course-org filtering, is
   the identity boundary. Every report's rows are post-filtered to the
   tenant's member set before being written to any sink -- each report
   exposes a different identity column (Student ID for grades, id for
   profiles, username for problem_responses, an anonymized-id/username
   match for ora2 -- see below). `may_enroll_info` cannot be filtered this
   way at all (see its own section below) and is excluded by default.
2. The course set is the union of org-filtered courses AND courses any
   member is actually enrolled in (`_get_tenant_course_ids`) -- org
   filtering alone misses a member's enrollment in a course outside the
   tenant's own orgs (cross-listed courses, legacy enrollments, etc.).
   Unlike `export_learner_data.py`'s `_get_course_ids` (which stringifies
   its output for JSON/display purposes only), this command's course-id
   resolver returns real `CourseKey` objects -- the five report generators
   below all take an actual `CourseKey`, not a string (confirmed: every
   test in `test_tasks_helper.py` passes `self.course.id`, a `CourseLocator`,
   not `str(self.course.id)`).

Output / merge design
=========================
Report headers are not stable across courses (grade-report headers vary by
each course's own graded-assignment/cohort/team structure; problem-response
columns vary by problem type). Each course's raw `(header, rows)` is
written untouched (full column fidelity, no forced alignment) to
`per_course/<course_id>__<report>.csv` -- filtered to tenant membership,
never unfiltered, even at this per-course level. A fixed, known-safe
column-name allowlist is additionally pulled into tenant-wide summary
files:
- `grades_summary.csv`: course_id + the identity/enrollment/certificate
  columns from `GRADES_SUMMARY_COLUMNS` (excludes per-course-varying
  assignment/cohort/team columns, which stay in the per-course file only).
- `learner_profile.csv`: one row per learner, deduped on `id` across every
  course (profile fields are learner-constants, not course-scoped).
- `course_enrollments.csv`: the course-varying profile columns
  (`enrollment_mode`, `verification_status`, `cohort`, `team`) as separate
  rows with `course_id` attached -- these three files all come from a
  single `upload_students_csv` call per course (see `GENERATOR_KEY_FOR_REPORT`).
- `problem_responses.csv` / `ora2_responses.csv`: learner-response
  granularity, concatenated with `course_id` prepended, no dedup. Because
  their per-course headers are genuinely not a fixed, predetermined
  constant (problem_responses' columns are xblock-dependent; ora2's exact
  schema is unverified in this checkout -- see below), these two sinks
  necessarily buffer every row in memory across the whole run and write
  once at the end (`_DictCsvSink`), unlike the fixed-header sinks above
  (`_CsvSink`), which stream one course at a time. This is bounded by the
  tenant's total response volume, not the OOM-class risk of accumulating
  multiple courses' *raw, pre-filtered* generator output simultaneously --
  the actual per-course generator calls still run, filter, and write their
  per-course file one course at a time; only the small merged summary rows
  are held past that point.
- `may_enroll_info.csv` (opt-in only, see below): a fixed, constant header
  (this command controls the requested feature list), so it streams like
  `grades_summary.csv`.
- `per_course/<course_id>__grades_errors.csv` (only written when
  `CourseGradeReport._upload` actually captured a `grade_report_err`
  upload): the students `CourseGradeFactory` failed to grade for that
  course, tenant-filtered the same as every other sink here. The grades
  status in the manifest also records two counts, which can legitimately
  differ: `failed` is `CourseGradeReport`'s own course-wide failure count
  (`context.task_progress.failed`), `failed_rows_exported` is how many of
  those rows are this tenant's and were actually written to the file above.

`MANIFEST.json['summary_files']` counts are read from each sink BEFORE that
sink's own `close()` runs (so a `close()` failure never costs an operator a
count that was already safely knowable) -- read this alongside
`summary_file_errors`, which is keyed by the same `<name>.csv` and only
present for a sink whose `close()` itself raised. A count in `summary_files`
for such a sink describes what it was *holding*, not necessarily what
successfully landed on disk; check `summary_file_errors` for that filename
before trusting the file is complete.

A failure to write MANIFEST.json, and a failure in any sink's own `close()`
(which can lose an entire summary CSV, not just this audit trail), are both
only ever swallowed (as a raised CommandError, that is -- see below for what
still reaches stderr regardless) when doing so would mask an original
in-flight exception (KeyboardInterrupt, MemoryError, ...); on an otherwise
successful run there is nothing to mask, so either failure is surfaced as a
CommandError instead of a clean exit -- a clean exit 0 that silently cost
every caveat, per-course status, and count documented above (a lost
MANIFEST.json), or a whole missing summary CSV (a lost sink), is worse than a
loud failure. The sink close loop always runs BEFORE the MANIFEST.json write
attempt, so `summary_file_errors` above is on disk in the written manifest
whenever that write itself succeeds; if both the manifest write AND a sink
close() fail on the same run, the MANIFEST.json write failure takes priority
for which exception actually propagates -- only one can. That does mean
`summary_file_errors` itself never reaches disk in that case (MANIFEST.json
never landed), but it is NOT lost entirely: the sink-failure stderr message
below is written unconditionally whenever `summary_file_errors` is
non-empty -- BEFORE either raise is decided, not after -- so which CSV(s)
were lost is still named on stderr, the operator's only remaining channel
for it once the manifest write itself has failed.

Per-course report failures (a `_run_*` method returning `{'status': 'error',
...}` for one course, without raising -- see "per-course orchestration"
below) are isolated from the run as a whole by design: a broken course must
not abort a run covering many courses. But that isolation must not let the
run's own top-level summary claim a plain, unconditional success either. Note
`manifest['courses_completed']` above answers a DIFFERENT question -- "was
this course actually attempted" (excludes only `--skip-course` entries) --
not "did it succeed"; a course with every report erroring still appears
there, since it genuinely was processed. Read it alongside
`manifest['courses_with_errors']` below for the latter.

`manifest['courses_with_errors']` (computed in the finally block, before
`_write_manifest` runs) lists every course with at least one report that
lost something -- either a report's own `status` is `'error'`, or a
nominally-`'success'` report recorded a secondary sub-artifact failure (only
`_run_grades`'s `grades_errors_export_error` does this today: the per-course
grades data itself was fine, but that course's own `grades_errors.csv`
never landed). `'skipped'` (only `_run_ora2`'s unverifiable-identity-column
outcome, see below) does NOT count as an error here -- it is a deliberate,
already-documented abstention from writing an unsafe unfiltered file, not a
failure to write something that should have succeeded. `--skip-course`
entries are never counted either (they were never actually processed). When
`courses_with_errors` is non-empty, `manifest['status']` is downgraded from
`'complete'` to `'complete_with_errors'` (an `'incomplete'` run -- one that
was itself interrupted -- is left as `'incomplete'`, a strictly worse signal
already), and the final stdout banner is a `self.style.WARNING(...)` naming
the affected-course count and pointing at `MANIFEST.json['courses']`,
instead of the plain unconditional success line.

A SEPARATE, weaker rollup, `manifest['courses_needing_review']`, catches two
outcomes that are NOT failures -- nothing raised, no report's own `status` is
`'error'`, so they do NOT touch `manifest['status']` or count toward
`courses_with_errors` above -- but still leave real data missing or suspect
for that course: a report carrying `_empty_filter_warning`'s `warning` key
(every row for that report was filtered out -- usually a sign the identity
column didn't actually match, not that the course genuinely has zero tenant
rows), and `_run_ora2`'s `'skipped'` outcome (that course's ora2 data is
absent from this export by deliberate design, but still absent). The final
stdout banner is qualified by EITHER rollup being non-empty, not just
`courses_with_errors` -- an operator must not have to know to go dig through
every per-course entry in `MANIFEST.json['courses']` to discover that a
course's data was quietly filtered to nothing or skipped outright.

`_open_sinks` itself raising partway through (e.g. a third sink's file
creation fails after two already succeeded) already surfaces loudly -- that
exception propagates all the way out of `handle()`, an already-loud failure,
not a silent one. But the sinks it had already created before that point
must not become orphans of it: `_open_sinks` mutates the SAME `sinks` dict
`handle()` already holds (rather than only building and returning a fresh
one), so those earlier sinks stay reachable to the finally block below even
though `_open_sinks` itself never returns normally -- they still get
`close()`d and still get counted in `summary_files`, instead of leaking their
file descriptors and vanishing from the audit trail of a run that already
failed.

Known limit inherited from the reused generator code, not fixable here:
`CourseGradeReport._generate` materializes a full course's enrollment batch
in memory regardless of caller. For a tenant with one very large course,
this is a real per-course memory ceiling this command's own streaming
discipline cannot fully paper over.

`learner_profile.csv` and `grades_summary.csv` can disagree on membership
=============================================================================
`learner_profile.csv` (via `upload_students_csv` -> `enrolled_students_features`,
`instructor_analytics/basic.py`) filters to `courseenrollment__is_active=1`.
`grades_summary.csv` (via `CourseGradeReport`, which calls
`users_enrolled_in(..., include_inactive=True)`) does not. A learner who
unenrolled from a course can therefore have a `grades_summary.csv` row with
no matching `learner_profile.csv` row for that same learner -- this is
inherited from the two upstream generators' own differing definitions of
"enrolled", not a bug in this command's merge logic, and is not resolved
here (an operator joining these two files by learner id should expect it).

CSV formula injection
=========================
All three CSV writers here (`_CsvSink`, `_DictCsvSink`, and
`_write_course_csv`'s raw writer) escape any header or data cell whose
string value starts with `=`, `+`, `-`, or `@` by prefixing it with a
single quote (`_escape_csv_formula`) -- `QUOTE_ALL` alone does not stop a
spreadsheet application from *evaluating* such a cell as a formula, and
learner-controlled fields (name, goals, mailing_address, free-text ORA/
problem-response answers) flow through every sink here. Headers are
included, not just data, because a per-course header is not always a
fixed constant this command controls (e.g. `CourseGradeReport`'s per-
assignment/experiment-partition names are course-author-supplied).

The `may_enroll_info` gap
============================
`may_enroll_info` reports on `CourseEnrollmentAllowed` rows: pending,
not-yet-registered invitees identified only by email, with no user account
and therefore no `EdlyMultiSiteAccess` row to filter against by definition.
It is excluded from the default `--reports` list; requesting it via
`--reports may_enroll` produces a file covering *every* pending invite for
the resolved courses, not just this tenant's -- this caveat is recorded in
the manifest and printed as a warning at runtime, not just documented here.

The ora2 identity-column gap -- CONFIRMED against the real schema, content
check kept as a safety net rather than the sole mechanism
=============================================================================
`openassessment`/`edx-ora2` is pinned in `requirements/edx/base.txt` as an
editable VCS install (`edly-io/edx-ora2@develop-koa`) and was NOT vendored
in the checkout this command was originally authored against, so
`OraAggregateData.collect_ora2_data`'s exact column layout could not be
read directly at the time. This has since been confirmed against the real
`edly-io/edx-ora2@develop-koa` schema: the real identity columns are
`Anonymized Student ID` (always present) and `Username` (present when
username-in-report is enabled) -- the earlier concern that a scorer column
could false-positive-match does not apply. This closes what was
previously an open, unverified gap.

`_detect_ora2_identity_column` still verifies a column *by its actual
content* against two known identity spaces this command DOES control:
every `student_anonymoususerid.anonymous_user_id` and every enrolled
username for that course -- kept intentionally as a sanity-check/safety
net rather than switched to a name-based lookup, since a content check
degrades safely (skips the course) if a future ora2 schema change ever
renames or removes these columns, where a bare name lookup would not. A
column qualifies only if every non-empty value in it is contained in one
of those sets. If detection finds no qualifying column, that course's
ora2 output is skipped entirely (not written unfiltered) and recorded in
the manifest as `skipped: identity column unverified`, with a loud log
line pointing at `--ora2-identity-column` as the override.

ORA2 file attachments are not exported
==========================================
Only the free-text response fields `OraAggregateData.collect_ora2_data`
returns are exported to `ora2_responses.csv` / `per_course/*__ora2.csv`.
Submission **file attachments** (`upload_ora2_submission_files`,
`tasks_helper/misc.py`) are a separate upload this command does not call
and are never included in this export. Flagged here as a product
question, not silently assumed acceptable: confirm this is fine for the
off-boarding use case before relying on this command as a complete ORA2
data export.

Secrets/PII default policy
==============================
None of these five report functions leak actual secrets by construction --
they build rows from curated column allowlists, not `SELECT *`. The one
real risk: the profile feature `meta` is a raw JSON blob on
`auth_userprofile` that arbitrary installed apps can stash anything into --
excluded from `DEFAULT_PROFILE_FEATURES` and from any `--include-fields`
override unless `--allow-meta-field` is also passed. Standard PII (email,
mailing address, DOB) is expected and wanted for a human-readable
off-boarding roster per the redirect decision and is not stripped by
default. Not resolved here, flagged as a product question: ORA2/
problem-response CSVs contain free-text learner-submitted answers, where
PII could appear inside the answer content itself (e.g. a name typed into
an essay) -- no column-level filter catches that.

Read-replica routing -- this command's own queries only, by design
========================================================================
This command's own direct queries (tenant/org/course/user resolution,
ORA2 identity-column detection) route through `.using(read_replica_or_default())`
(`common.djangoapps.util.query`, the same pattern `cache_programs.py` uses)
so a large tenant export doesn't add read load to the primary database.

The five upstream instructor-report generators themselves are deliberately
NOT forced onto the replica -- this is a known, permanent limitation, not
an oversight. `CourseGradeReport.generate` has a confirmed read-then-write
pattern (a grade-cache miss triggers an in-request recompute-and-persist),
which makes a replica-lag-sensitive read there actively unsafe (a stale
read could recompute and persist a grade from lagging data); the other
four generators are unverified for the same pattern and are left on
whatever routing they already use rather than guessing.

Consequence -- this command's own queries can be staler than the generator
output they filter. The generators read the primary; membership/identity
resolution here reads the replica. Under replica lag: a recently added
tenant member is silently omitted from every filtered file, and
`_detect_ora2_identity_column`'s all-values-must-match check can fail on a
single unreplicated anon id, skipping that course's ora2 output entirely
(recorded as 'identity column unverified'). For an off-boarding export,
either run against a quiesced tenant or accept this window.

Usage:
    python manage.py export_tenant_reports_csv <slug> --dry-run
    python manage.py export_tenant_reports_csv <slug>
    python manage.py export_tenant_reports_csv <slug> --reports grades,profiles
    python manage.py export_tenant_reports_csv <slug> --as-user staff_user --reports problem_responses
"""

import csv
import getpass
import json
import logging
import os
import re
from contextlib import contextmanager
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from six import text_type

from common.djangoapps.student.models import AnonymousUserId, CourseEnrollment
from common.djangoapps.util.query import read_replica_or_default
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from openedx.features.edly.models import EdlyMultiSiteAccess, EdlySubOrganization
from xmodule.modulestore.django import modulestore

from lms.djangoapps.instructor_task.tasks_helper import enrollments as enrollments_module
from lms.djangoapps.instructor_task.tasks_helper import grades as grades_module
from lms.djangoapps.instructor_task.tasks_helper import misc as misc_module
from lms.djangoapps.instructor_task.tasks_helper import runner as runner_module
from lms.djangoapps.instructor_task.tasks_helper.utils import UPDATE_STATUS_FAILED

logger = logging.getLogger(__name__)

# --reports vocabulary. 'may_enroll' is deliberately excluded from
# DEFAULT_REPORTS -- see the module docstring's "may_enroll_info gap".
REPORT_CHOICES = ('grades', 'profiles', 'enrollments', 'problem_responses', 'ora2', 'may_enroll')
DEFAULT_REPORTS = ('grades', 'profiles', 'enrollments', 'problem_responses', 'ora2')

# 'profiles' and 'enrollments' are two different tenant-wide *outputs* built
# from the same single `upload_students_csv` call per course -- see the
# module docstring's merge design.
GENERATOR_KEY_FOR_REPORT = {
    'grades': 'grades',
    'profiles': 'student_features',
    'enrollments': 'student_features',
    'problem_responses': 'problem_responses',
    'ora2': 'ora2',
    'may_enroll': 'may_enroll',
}

# Profile-constant columns (learner-level, not course-scoped) -- 'meta' is
# deliberately absent (see module docstring); 'id' is forced into every
# resolved feature list regardless of overrides, since it's both the
# membership-filter column and the learner_profile.csv dedup key.
DEFAULT_PROFILE_FEATURES = (
    'id', 'username', 'name', 'email', 'language', 'location', 'year_of_birth',
    'gender', 'level_of_education', 'mailing_address', 'goals', 'last_login', 'date_joined',
)
# Course-varying profile columns -- routed to course_enrollments.csv instead
# of learner_profile.csv (see module docstring).
COURSE_VARYING_PROFILE_FEATURES = ('enrollment_mode', 'verification_status', 'cohort', 'team')

# Fixed default feature request for --reports may_enroll -- CourseEnrollmentAllowed's
# own schema (email/auto_enroll/created), not learner PII beyond an email address.
MAY_ENROLL_DEFAULT_FEATURES = ('email', 'auto_enroll', 'created')

# Fixed subset of CourseGradeReport's header that's stable across every
# course (the rest of that header -- per-assignment/cohort/team columns --
# varies by course and stays in the per-course file only). 'Grade' sits at
# the same position CourseGradeReport._grades_header always emits it at,
# right after the three identity columns -- omitting it here previously
# meant grades_summary.csv had no grade in it at all.
GRADES_SUMMARY_COLUMNS = [
    'Student ID', 'Email', 'Username', 'Grade', 'Enrollment Track', 'Verification Status',
    'Certificate Eligible', 'Certificate Delivered', 'Certificate Type', 'Enrollment Status',
]

MANIFEST_FILENAME = 'MANIFEST.json'


@contextmanager
def _celery_free_context():
    """
    Patch the one sanctioned test seam (see module docstring) so the five
    report-generator functions' internal `TaskProgress.update_task_state`
    calls work outside a real Celery worker.
    """
    with patch.object(runner_module, '_get_current_task', return_value=Mock(update_state=Mock())):
        yield


@contextmanager
def _capture_csv_uploads(buffer):
    """
    Patch `upload_csv_to_report_store` in all three modules that import
    their own name binding of it (see module docstring), appending every
    call made during the `with` block to `buffer` instead of touching
    `ReportStore`/S3 at all.
    """
    def _capture(rows, csv_name, course_id, timestamp, config_name='GRADES_DOWNLOAD'):
        buffer.append({'csv_name': csv_name, 'course_id': course_id, 'rows': list(rows)})
        return csv_name

    with patch.object(grades_module, 'upload_csv_to_report_store', _capture), \
            patch.object(enrollments_module, 'upload_csv_to_report_store', _capture), \
            patch.object(misc_module, 'upload_csv_to_report_store', _capture):
        yield


@contextmanager
def _override_max_problem_responses_limit(override_value):
    """
    Temporarily override FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] (a hard
    5000-responses-per-course default) to `override_value` (None means "no
    cap") for the duration of a run -- restoring the original value
    afterward even if the run raises. Opt-in only, via --max-problem-
    responses (see add_arguments/handle): this is NOT applied automatically
    just because problem_responses was requested -- lifting the only bound
    on that report's memory footprint is a deliberate operator decision,
    not a default.
    """
    original = settings.FEATURES.get('MAX_PROBLEM_RESPONSES_COUNT')
    logger.warning(
        "export_tenant_reports_csv: overriding FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] "
        "to %r for this run (was %r) -- requested via --max-problem-responses.",
        override_value, original,
    )
    settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] = override_value
    try:
        yield
    finally:
        settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] = original


@contextmanager
def _null_context():
    """
    No-op context manager for when the --max-problem-responses override
    isn't requested (or doesn't apply) and
    `_override_max_problem_responses_limit` shouldn't run at all.
    """
    yield


def _safe_course_id(course_id):
    """
    Sanitize a course id for use in a filename -- same characters
    `ProblemResponses._generate_upload_file_name` strips.
    """
    return re.sub(r'[:/+]', '_', text_type(course_id))


# Characters a spreadsheet application (Excel, Sheets, etc.) will interpret
# as a formula trigger if they lead a cell's content.
_CSV_FORMULA_INJECTION_PREFIXES = ('=', '+', '-', '@')


def _escape_csv_formula(value):
    """
    Prefix a string value with a single quote if it starts with a formula
    trigger character -- QUOTE_ALL alone does not stop a spreadsheet app
    from *evaluating* such a cell (CSV formula injection), and learner-
    controlled fields (name, goals, mailing_address, free-text ORA/
    problem-response answers) flow through every sink here unescaped
    otherwise. Applied to every header AND data cell at each of this
    file's three write points (`_CsvSink`, `_DictCsvSink`, and
    `_write_course_csv`'s raw writer) -- not just data rows, since a
    per-course header can itself be course-author/xblock-supplied (e.g.
    CourseGradeReport's per-assignment/experiment-partition names), not a
    fixed constant this command controls. The leading apostrophe is the
    standard mitigation (it tells the spreadsheet app "treat this as text,
    not a formula") and is invisible once opened in Excel/Sheets; the one
    visible side effect is that a stringified value starting with '-'
    (e.g. a negative number rendered as text) also gets the prefix -- an
    accepted trade-off, not a data-fidelity bug.
    """
    if isinstance(value, text_type) and value.startswith(_CSV_FORMULA_INJECTION_PREFIXES):
        return u"'" + value
    return value


class _CsvSink(object):
    """
    A tenant-wide summary CSV whose column set is a fixed, predetermined
    constant -- written once, then appended to one course at a time as the
    run progresses (see module docstring: this is the streaming half of
    the merge design, as opposed to `_DictCsvSink` below).
    """

    def __init__(self, path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self._file = os.fdopen(fd, 'w', newline='', encoding='utf-8')
        self._writer = csv.writer(self._file, dialect='excel', quotechar='"', quoting=csv.QUOTE_ALL)
        self._header_written = False
        self.row_count = 0

    def write_header(self, header):
        if not self._header_written:
            self._writer.writerow([_escape_csv_formula(value) for value in header])
            self._header_written = True

    def write_rows(self, rows):
        for row in rows:
            self._writer.writerow([_escape_csv_formula(value) for value in row])
            self.row_count += 1
        self._file.flush()

    def close(self):
        self._file.close()


class _DictCsvSink(object):
    """
    A tenant-wide summary sink for report types whose per-course header is
    NOT a predetermined constant (problem_responses' columns depend on
    each course's xblock types; ora2's exact schema is unverified in this
    checkout -- see module docstring). A flat CSV needs one fixed column
    set decided before its header row is written, and that set can only be
    known once every course's header has been seen -- so, unlike
    `_CsvSink`, this buffers every row (as a dict, keyed by that course's
    own header) in memory across the whole run and writes once at `close`.
    Bounded by this tenant's total response/assessment volume -- not the
    OOM-class risk `_CsvSink`'s immediate-flush avoids, which is about
    accumulating multiple courses' *raw, pre-filtered* generator output
    simultaneously. The actual per-course generator call, filter, and
    per-course file write still happen one course at a time; only these
    already-filtered, tenant-scoped summary rows are held past that point.
    """

    def __init__(self, path):
        self._path = path
        self._fieldnames = []
        self._rows = []

    def add_rows(self, course_id, header, rows):
        for column in ['course_id'] + list(header):
            if column not in self._fieldnames:
                self._fieldnames.append(column)
        course_id_str = text_type(course_id)
        for row in rows:
            entry = dict(zip(header, row))
            entry['course_id'] = course_id_str
            self._rows.append(entry)

    @property
    def row_count(self):
        return len(self._rows)

    def close(self):
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as output_file:
            writer = csv.DictWriter(
                output_file, fieldnames=self._fieldnames, restval='', extrasaction='ignore',
                dialect='excel', quotechar='"', quoting=csv.QUOTE_ALL,
            )
            # Not writer.writeheader() -- these fieldnames come from each
            # course's own report header (xblock/ora2 column names), not a
            # fixed constant this command controls, so they need the same
            # formula-injection escaping as any other cell (see
            # _escape_csv_formula). writerow() on a {name: escaped_name}
            # dict writes the header row in fieldname order same as
            # writeheader() would.
            writer.writerow({name: _escape_csv_formula(name) for name in self._fieldnames})
            for entry in self._rows:
                writer.writerow({key: _escape_csv_formula(value) for key, value in entry.items()})


class Command(BaseCommand):
    """
    Export one Edly tenant's off-boarding data as human-readable, tenant-
    membership-filtered CSVs (grades, profiles/enrollments, problem
    responses, ORA2 responses, and optionally may-enroll invites), by
    calling the LMS's own per-course instructor-report generators directly
    in-process -- see module docstring for the full design and known gaps.
    """
    help = (
        "Export one Edly tenant's data as human-readable CSV reports for off-boarding, "
        "reusing the LMS's own instructor-report generators (grades/profiles/enrollments/"
        "problem_responses/ora2, plus optional may_enroll)."
    )

    def add_arguments(self, parser):
        """
        Add command line arguments.
        """
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument(
            '--output-dir',
            default=None,
            help='Directory to write into (default: EDM_EXPORT_DIR or '
                 '<MEDIA_ROOT>/edm_exports/<slug>_<timestamp>_csv/).',
        )
        parser.add_argument(
            '--reports',
            default=','.join(DEFAULT_REPORTS),
            help='Comma-separated report types to generate. Choices: {0}. '
                 'Default excludes may_enroll -- see module docstring.'.format(', '.join(REPORT_CHOICES)),
        )
        parser.add_argument(
            '--as-user',
            default=None,
            help='Username or email of the acting staff identity. Required if problem_responses is '
                 'requested (drives its get_course_blocks() access resolution); also recorded in the '
                 'manifest as the run operator when given.',
        )
        parser.add_argument(
            '--include-fields',
            default=None,
            help='Comma-separated override of the default profile feature allowlist. '
                 "'meta' requires --allow-meta-field to also be passed.",
        )
        parser.add_argument(
            '--allow-meta-field',
            action='store_true',
            help="Confirm that --include-fields is intentionally requesting the raw 'meta' JSON blob.",
        )
        parser.add_argument(
            '--skip-course',
            action='append',
            default=[],
            help='Course id to skip (repeatable escape hatch).',
        )
        parser.add_argument(
            '--ora2-identity-column',
            default=None,
            help='Column name in the ora2 report known (from a real devstack) to hold an anonymized '
                 'student id or username -- bypasses the runtime content-based detection (see module '
                 'docstring\'s ora2 gap).',
        )
        parser.add_argument(
            '--max-problem-responses',
            default=None,
            help="Opt-in override for FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] (a hard 5000-response-"
                 'per-course default) for the duration of a run that requests problem_responses. Not '
                 "applied automatically just because problem_responses was requested -- pass an integer "
                 "cap, or the literal 'unlimited' to remove the cap entirely (the old automatic-lift "
                 'behavior). Default: leave the platform-configured cap untouched.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Resolve + print the (course x report-type) matrix and resolved member/course counts. '
                 'Generates nothing.',
        )

    def handle(self, *args, **options):
        """
        Resolve the tenant's members and courses, then generate the requested reports.
        """
        slug = options['slug']
        dry_run = options['dry_run']
        reports = self._parse_reports(options['reports'])
        skip_courses = set(options.get('skip_course') or [])
        include_fields = self._resolve_include_fields(options.get('include_fields'), options.get('allow_meta_field'))
        ora2_identity_column = options.get('ora2_identity_column')
        max_problem_responses = self._resolve_max_problem_responses(options.get('max_problem_responses'))

        self._print_header(slug, reports, dry_run)

        sub_org = self._get_sub_org(slug)
        orgs = self._resolve_orgs(sub_org)
        user_ids = self._get_user_ids(sub_org)
        course_ids = self._get_tenant_course_ids(orgs, user_ids)

        self.stdout.write(u"Orgs: {0}".format(orgs))
        self.stdout.write(u"Courses resolved (org-filtered union member-enrolled): {0}".format(len(course_ids)))
        self.stdout.write(u"Members resolved: {0}".format(len(user_ids)))
        self.stdout.write(u"Reports requested: {0}".format(', '.join(reports)))

        if not user_ids:
            self.stdout.write(self.style.WARNING("No users found for this tenant -- nothing to export."))
            return

        operator_user = None
        if options.get('as_user'):
            operator_user = self._resolve_operator(options['as_user'])
        if 'problem_responses' in reports:
            if operator_user is None:
                raise CommandError("--as-user is required when --reports includes problem_responses.")
            if not operator_user.is_staff:
                raise CommandError(
                    u"--as-user '{0}' is not a staff user -- ProblemResponses._build_student_data's "
                    u"get_course_blocks() call would silently prune the block tree to what that user "
                    u"can see, undercounting problem_responses for a non-staff operator. Pass a staff "
                    u"account via --as-user.".format(operator_user.username)
                )

        if 'may_enroll' in reports:
            self.stdout.write(self.style.WARNING(
                "may_enroll requested -- these rows cannot be tenant-membership-filtered "
                "(CourseEnrollmentAllowed rows are pre-registration invites with no user account); "
                "the output will include every pending invite for these courses, not just this tenant's."
            ))

        if dry_run:
            self._dry_run(course_ids, reports)
            return

        timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
        output_dir = self._resolve_output_dir(slug, options.get('output_dir'), timestamp)
        per_course_dir = os.path.join(output_dir, 'per_course')
        self._make_private_dir(output_dir)
        self._make_private_dir(per_course_dir)

        tenant_usernames = set(
            get_user_model().objects.using(read_replica_or_default())
            .filter(id__in=user_ids).values_list('username', flat=True)
        )
        feature_list = self._build_feature_list(include_fields, reports)

        manifest = {
            'slug': slug,
            'operator': operator_user.username if operator_user else getpass.getuser(),
            'generated_at': timezone.now().isoformat(),
            'reports_requested': list(reports),
            'overrides': {
                'include_fields': include_fields,
                'allow_meta_field': bool(options.get('allow_meta_field')),
                # None when --max-problem-responses wasn't passed (the cap is opt-in, not
                # automatic just because problem_responses was requested -- see add_arguments).
                'max_problem_responses_override': max_problem_responses,
                'ora2_identity_column_override': ora2_identity_column,
            },
            'courses': {},
            'summary_files': {},
            'known_gaps': {},
        }
        # known_gaps entries are NOT gated on which reports were requested as a whole --
        # the active/inactive enrollment-membership mismatch between learner_profile.csv
        # and grades_summary.csv (see module docstring) affects any plain --reports
        # grades,profiles run, not just when ora2 is requested -- previously this caveat
        # was only ever surfaced when ora2 was in the requested reports, so an operator
        # running grades+profiles alone never saw it in MANIFEST.json at all, only in the
        # module docstring.
        if 'grades' in reports and ('profiles' in reports or 'enrollments' in reports):
            manifest['known_gaps']['enrollment_membership_mismatch'] = (
                "learner_profile.csv (upload_students_csv -> enrolled_students_features) filters to "
                "courseenrollment__is_active=1; grades_summary.csv (CourseGradeReport -> "
                "users_enrolled_in(include_inactive=True)) does not. A learner who unenrolled can have "
                "a grades_summary.csv row with no matching learner_profile.csv row -- inherited from the "
                "two upstream generators' differing definitions of 'enrolled' (see module docstring)."
            )
        if 'ora2' in reports:
            manifest['known_gaps'].update({
                'ora2_identity_column_detection': (
                    "The ora2 identity column ('Anonymized Student ID', or 'Username' when enabled) "
                    "is detected at runtime by content against known identity spaces, kept "
                    "intentionally as a safety net rather than a bare name lookup -- courses where "
                    "detection fails are recorded below as 'skipped: identity column unverified', not "
                    "silently included unfiltered (see module docstring's ora2 identity-column note)."
                ),
                'ora2_file_attachments_not_exported': (
                    "Only free-text ORA responses are exported -- submission file attachments "
                    "(upload_ora2_submission_files) are never included (see module docstring)."
                ),
            })

        seen_learner_ids = set()
        if 'problem_responses' in reports and max_problem_responses is not None:
            resolved_cap = None if max_problem_responses == 'unlimited' else max_problem_responses
            max_problem_responses_ctx = _override_max_problem_responses_limit(resolved_cap)
        else:
            max_problem_responses_ctx = _null_context()

        # Wrapped in try/finally (not just a bare loop) so a Ctrl-C (KeyboardInterrupt) or
        # an in-process MemoryError partway through a multi-course run still closes every
        # open sink -- note an external OOM-kill is SIGKILL and no finally block can survive
        # that; only an in-process KeyboardInterrupt/MemoryError are actually caught here.
        # _DictCsvSink (problem_responses/ora2) buffers entirely in memory and only touches
        # disk in close(), so without this, an interrupted run previously lost those two
        # files entirely and left no manifest at all. 'status'/'courses_completed' below
        # let an operator tell an interrupted run's output apart from a complete one.
        # run_succeeded tracks whether control reaches the finally block WITHOUT an
        # exception in flight -- the finally block's own _write_manifest guard below
        # needs this to tell "nothing to mask, surface a manifest-write failure loudly"
        # apart from "an original exception is already propagating, don't mask it".
        # sinks is pre-initialized to {} and _open_sinks() itself is called as the FIRST
        # statement inside this try (not before it, as it previously was) -- so that if
        # opening a sink raises partway through (e.g. a third sink's file creation fails
        # after two already succeeded), this finally block still runs and MANIFEST.json
        # still gets written recording an incomplete run, instead of the whole try/finally
        # never even being entered. The same pre-initialized `sinks` dict is also passed
        # INTO _open_sinks (mutated in place, see its own docstring) rather than only
        # using whatever it returns -- so a sink or two already created before a LATER
        # sink's creation raises still land in this same `sinks` dict, and still get
        # closed/counted below, instead of being orphaned along with the exception.
        sinks = {}
        run_succeeded = False
        try:
            sinks = self._open_sinks(output_dir, reports, feature_list, sinks)
            with max_problem_responses_ctx:
                for course_id in sorted(course_ids, key=text_type):
                    course_key_str = text_type(course_id)
                    if course_key_str in skip_courses:
                        manifest['courses'][course_key_str] = {'_skipped': '--skip-course'}
                        self.stdout.write(u"  {0}: skipped (--skip-course)".format(course_key_str))
                        continue
                    status = self._process_course(
                        course_id, reports, user_ids, tenant_usernames, feature_list, operator_user,
                        ora2_identity_column, per_course_dir, sinks, seen_learner_ids,
                    )
                    manifest['courses'][course_key_str] = status
                    self.stdout.write(u"  {0}: {1}".format(course_key_str, status))
            manifest['status'] = 'complete'
            run_succeeded = True
        finally:
            # Only reached with run_succeeded still False if the loop (or the
            # max_problem_responses_ctx it's nested in) exited via an exception --
            # including KeyboardInterrupt/SystemExit, which `except Exception` inside
            # each _run_* method does NOT catch.
            manifest.setdefault('status', 'incomplete')
            manifest['courses_completed'] = [
                course_key_str for course_key_str, course_status in manifest['courses'].items()
                if '_skipped' not in course_status
            ]
            # Per-course report failures (a _run_* method returning {'status': 'error', ...}
            # for one course, without raising) are correctly isolated from the run as a
            # whole -- a broken course must not abort a run covering many courses -- but
            # that isolation must not let the run's OWN top-level summary claim a plain,
            # unconditional success when courses actually lost data. Computed here, BEFORE
            # _write_manifest below, so it's on disk in the written manifest. See module
            # docstring for what counts as an error here (and why 'skipped' does not).
            manifest['courses_with_errors'] = sorted(
                course_key_str for course_key_str, course_status in manifest['courses'].items()
                if self._course_has_report_error(course_status)
            )
            if manifest['courses_with_errors'] and manifest['status'] == 'complete':
                # Only downgrade a plain 'complete' -- an already-'incomplete' run (this
                # run was itself interrupted) is a strictly worse, more specific signal
                # and must not be overwritten by this weaker one.
                manifest['status'] = 'complete_with_errors'
            # A SEPARATE, weaker rollup from courses_with_errors above -- nothing here
            # actually failed (an all-rows-filtered warning, or ora2's deliberate
            # identity-column-unverified skip), so this does NOT touch manifest['status']
            # -- but real data is still missing/suspect for that course, and an operator
            # must not have to know to go dig through every per-course entry in
            # manifest['courses'] to find that out. See _course_needs_review's docstring.
            manifest['courses_needing_review'] = sorted(
                course_key_str for course_key_str, course_status in manifest['courses'].items()
                if self._course_needs_review(course_status)
            )
            # Each sink's own close() is real work that can itself fail --
            # _DictCsvSink.close() opens the output file and encodes/writes every
            # buffered row, and _CsvSink.close() flushes+closes an open fd. Without
            # isolating each one, a single sink failing to flush would abort this
            # loop before the remaining sinks close or the manifest below gets
            # written -- exactly the failure this finally block exists to prevent.
            for name, sink in sinks.items():
                try:
                    # Read the count BEFORE close() -- it's already known (a plain int
                    # attribute on _CsvSink, len(self._rows) on _DictCsvSink; neither can
                    # raise today) -- for _DictCsvSink specifically it's the size of the
                    # buffer close() is about to write, so a close() failure shouldn't
                    # cost the operator this number too. Reading it inside this try (not
                    # just before it) is defensive for a hypothetical future sink type
                    # whose row_count computation could itself raise.
                    manifest['summary_files'][name + '.csv'] = sink.row_count
                    sink.close()
                except Exception as exc:  # pylint: disable=broad-except
                    # Known, deliberate trade-off, not addressed here: a KeyboardInterrupt
                    # raised specifically during THIS sink's close() is a BaseException, not
                    # an Exception, so it escapes this guard entirely -- the remaining sinks
                    # never get a chance to close and the manifest write below is skipped.
                    logger.exception("export_tenant_reports_csv: failed to close sink %s", name)
                    manifest.setdefault('summary_file_errors', {})[name + '.csv'] = text_type(exc)

            # Computed here, right after the sink-close loop above (so it's fully
            # populated), rather than after the manifest-write attempt below -- the
            # manifest-write-failure message just below needs to already know whether a
            # sink-failure stderr message will actually follow it, to avoid pointing an
            # operator "(see below)" at nothing.
            sink_errors = manifest.get('summary_file_errors') or {}

            # manifest_write_error is captured rather than raised immediately so the
            # raise-or-swallow decision below can weigh it together with any sink close()
            # failure recorded above (already fully populated, see sink_errors just
            # above) -- so _write_manifest always runs BEFORE either raise below,
            # landing summary_file_errors on disk first whenever the write succeeds.
            manifest_write_error = None
            try:
                self._write_manifest(output_dir, manifest)
            except Exception as exc:  # pylint: disable=broad-except
                manifest_write_error = exc
                logger.exception(
                    "export_tenant_reports_csv: failed to write MANIFEST.json to %s", output_dir,
                )
                self.stderr.write(self.style.ERROR(
                    u"MANIFEST.json could not be written to {0}: {1} -- this run has no audit "
                    u"trail (report caveats, skipped-course records, and summary counts are all "
                    u"lost); any sink failure is reported on stderr only, not in the missing "
                    u"manifest{2}.".format(
                        output_dir, exc,
                        # Only promise a following stderr message if one will actually be
                        # written just below -- an empty sink_errors means it won't be.
                        u" (see below)" if sink_errors else u"",
                    )
                ))

            # Emitted unconditionally whenever a sink failed to close -- NOT gated on
            # run_succeeded below, and written BEFORE either raise is decided. A stderr
            # write cannot mask a propagating exception (only the raises below need that
            # gate), and this is the operator's ONLY remaining channel for which CSV(s)
            # were lost once the manifest write has ALSO failed (see module docstring):
            # the priority raise below can only let ONE exception through, so without
            # this, naming the lost sink here would otherwise be unreachable on that path.
            if sink_errors:
                manifest_pointer = (
                    u" -- see MANIFEST.json['summary_file_errors']." if manifest_write_error is None
                    # Don't point an operator at a MANIFEST.json that was never written.
                    else u" -- MANIFEST.json was not written this run (see above), so this "
                         u"is the only record of it."
                )
                self.stderr.write(self.style.ERROR(
                    u"These summary CSVs failed to flush/close and are missing or truncated "
                    u"on disk: {0}{1}".format(u', '.join(sorted(sink_errors)), manifest_pointer)
                ))

            if run_succeeded:
                # Nothing is propagating out of this finally block, so nothing can be
                # masked -- an operator must not get a clean exit 0 either for an export
                # with no audit trail or for one that silently lost a whole summary CSV.
                if manifest_write_error is not None:
                    # Manifest failure takes priority when both fail -- only one exception
                    # can propagate, and this one also means summary_file_errors itself
                    # never made it to disk (MANIFEST.json never landed) -- but the sink
                    # name(s) it held were already put on stderr above regardless.
                    raise CommandError(
                        u"Export failed to write MANIFEST.json: {0}".format(manifest_write_error)
                    )
                if sink_errors:
                    raise CommandError(
                        u"Export completed but {0} summary CSV(s) failed to write: {1}".format(
                            len(sink_errors), u', '.join(sorted(sink_errors))
                        )
                    )
            # Otherwise an original exception (KeyboardInterrupt, MemoryError, a plain
            # Exception from _process_course, ...) is already propagating out of this
            # finally block -- swallow these secondary failures so they can't replace that
            # original, the operator's only signal for why the run actually stopped.

        courses_with_errors = manifest.get('courses_with_errors') or []
        courses_needing_review = manifest.get('courses_needing_review') or []
        self.stdout.write("\n" + "=" * 72)
        if courses_with_errors or courses_needing_review:
            # Nothing above raised (run_succeeded, no manifest-write/sink-close failure),
            # but either rollup being non-empty means this run must not read as a plain,
            # unconditional success -- see module docstring's courses_with_errors /
            # courses_needing_review sections. Composed rather than two separate
            # `if`/`elif` banners so a run hitting BOTH still gets a single message
            # naming both counts, not just whichever branch happened to come first.
            parts = []
            if courses_with_errors:
                parts.append(u"{0} course(s) had at least one report error".format(len(courses_with_errors)))
            if courses_needing_review:
                parts.append(u"{0} course(s) have a warning or skipped report worth reviewing".format(
                    len(courses_needing_review)
                ))
            self.stdout.write(self.style.WARNING(
                u"Tenant CSV export for {0} finished, but {1} -- see MANIFEST.json['courses'] "
                u"in {2} for detail.".format(slug, u' and '.join(parts), output_dir)
            ))
        else:
            self.stdout.write(u"Exported tenant CSV reports for {0} to {1}".format(slug, output_dir))
        self.stdout.write("=" * 72)

    # ------------------------------------------------------------------ tenant scoping
    # _get_sub_org / _resolve_orgs / _get_user_ids reuse export_learner_data.py's pattern
    # verbatim (see module docstring). _get_tenant_course_ids is the amended course-set
    # union (structural fix #2) -- NOT export_learner_data.py's _get_course_ids, whose
    # stringified output is display/JSON-only; the report generators below need real
    # CourseKey objects.

    def _get_sub_org(self, slug):
        """
        Resolve the EdlySubOrganization for the given slug.
        """
        try:
            return EdlySubOrganization.objects.using(read_replica_or_default()).get(slug=slug)
        except EdlySubOrganization.DoesNotExist:
            raise CommandError(u"No EdlySubOrganization found for slug '{0}'.".format(slug))

    def _resolve_orgs(self, sub_org):
        """
        Resolve the tenant's course_org_filter (used only to seed the
        org-filtered half of `_get_tenant_course_ids`'s union).
        """
        orgs = []
        try:
            site_configuration = sub_org.lms_site.configuration
        except SiteConfiguration.DoesNotExist:
            site_configuration = None

        if site_configuration:
            course_org_filter = site_configuration.get_value('course_org_filter', [])
            if course_org_filter:
                orgs = course_org_filter if isinstance(course_org_filter, list) else [course_org_filter]

        if not orgs:
            orgs = sub_org.get_edx_organizations

        return list(orgs)

    def _get_user_ids(self, sub_org):
        """
        Resolve the tenant's user ids strictly via EdlyMultiSiteAccess
        membership -- the sole tenant boundary this command recognizes.
        """
        return set(
            EdlyMultiSiteAccess.objects.using(read_replica_or_default())
            .filter(sub_org=sub_org).values_list('user_id', flat=True)
        )

    def _get_tenant_course_ids(self, orgs, user_ids):
        """
        Amended course-set union (structural fix #2, see module docstring):
        org-filtered courses alone miss a member's enrollments in courses
        outside the tenant's own org list. Returns real CourseKey objects.
        """
        org_course_ids = set(
            CourseOverview.objects.using(read_replica_or_default())
            .filter(org__in=orgs).values_list('id', flat=True)
        )
        member_course_ids = set(
            CourseEnrollment.objects.using(read_replica_or_default())
            .filter(user_id__in=user_ids, is_active=True)
            .values_list('course_id', flat=True).distinct()
        )
        return org_course_ids | member_course_ids

    def _resolve_operator(self, identifier):
        """
        Resolve --as-user by username or email.
        """
        User = get_user_model()
        try:
            return User.objects.using(read_replica_or_default()).get(Q(username=identifier) | Q(email=identifier))
        except User.DoesNotExist:
            raise CommandError(u"--as-user '{0}' does not match any user (by username or email).".format(identifier))
        except User.MultipleObjectsReturned:
            raise CommandError(u"--as-user '{0}' matches multiple users -- be more specific.".format(identifier))

    # ------------------------------------------------------------------ argument resolution

    def _parse_reports(self, reports_arg):
        """
        Parse + validate --reports against REPORT_CHOICES.
        """
        requested = [r.strip() for r in reports_arg.split(',') if r.strip()]
        unknown = set(requested) - set(REPORT_CHOICES)
        if unknown:
            raise CommandError(u"Unknown --reports value(s): {0}. Choose from: {1}".format(
                ', '.join(sorted(unknown)), ', '.join(REPORT_CHOICES)
            ))
        return requested

    def _resolve_include_fields(self, include_fields_arg, allow_meta_field):
        """
        Parse --include-fields, guard 'meta' behind --allow-meta-field, and
        force 'id' into the list regardless of the override (see module
        docstring -- it's the membership-filter/dedup key, not optional).
        """
        fields = (
            [f.strip() for f in include_fields_arg.split(',') if f.strip()]
            if include_fields_arg else list(DEFAULT_PROFILE_FEATURES)
        )
        if 'meta' in fields and not allow_meta_field:
            raise CommandError(
                "--include-fields requested 'meta' -- pass --allow-meta-field to confirm this is "
                "intended (see module docstring's secrets/PII policy)."
            )
        if 'id' not in fields:
            fields = ['id'] + fields
        return fields

    def _resolve_max_problem_responses(self, max_problem_responses_arg):
        """
        Parse --max-problem-responses: None if not passed (leave the
        platform's configured cap untouched -- see
        _override_max_problem_responses_limit / handle), the literal
        'unlimited' (explicit opt-in to the old automatic-lift behavior),
        or an integer override.
        """
        if max_problem_responses_arg is None:
            return None
        if max_problem_responses_arg == 'unlimited':
            return 'unlimited'
        try:
            return int(max_problem_responses_arg)
        except ValueError:
            raise CommandError(
                u"--max-problem-responses must be an integer or 'unlimited', got '{0}'.".format(
                    max_problem_responses_arg
                )
            )

    def _build_feature_list(self, include_fields, reports):
        """
        The feature list actually requested from upload_students_csv: the
        resolved --include-fields, plus the course-varying columns if
        'enrollments' was requested.
        """
        feature_list = list(include_fields)
        if 'enrollments' in reports:
            for feature in COURSE_VARYING_PROFILE_FEATURES:
                if feature not in feature_list:
                    feature_list.append(feature)
        return feature_list

    # ------------------------------------------------------------------ output plumbing

    def _resolve_output_dir(self, slug, output_dir, timestamp):
        """
        `--output-dir` wins verbatim; otherwise EDM_EXPORT_DIR/<slug>_<timestamp>_csv/,
        falling back to a directory under MEDIA_ROOT. The '_csv' suffix (vs.
        export_learner_data.py's bare <slug>_<timestamp>) avoids a MANIFEST.json
        collision if both commands are ever run for the same tenant in the same second.
        """
        if output_dir:
            return output_dir
        base = getattr(settings, 'EDM_EXPORT_DIR', os.path.join(settings.MEDIA_ROOT, 'edm_exports'))
        return os.path.join(base, u"{0}_{1}_csv".format(slug, timestamp))

    def _make_private_dir(self, path):
        """
        Create `path` (and any missing parents) and force 0700 perms on it
        -- these directories hold PII (grades, profiles, free-text ORA
        responses). Only chmods a directory this call actually created:
        `--output-dir` can point at a path that already existed before this
        run (e.g. MEDIA_ROOT itself), and unconditionally chmod'ing it would
        silently lock that shared directory down for every other consumer.
        """
        if not os.path.exists(path):
            os.makedirs(path, mode=0o700)
            os.chmod(path, 0o700)

    def _open_sinks(self, output_dir, reports, feature_list, sinks=None):
        """
        Open the tenant-wide summary sinks for the requested reports and
        write their headers up front where the header is a fixed constant
        (see module docstring: _CsvSink vs _DictCsvSink).

        Mutates (and returns) `sinks` in place rather than only building and
        returning a fresh dict -- `handle()` passes in the same dict it
        already holds so that if creating a LATER sink here raises (e.g. a
        third sink's file creation fails after two already succeeded), the
        EARLIER sinks already added above stay visible to the caller even
        though this call itself never returns normally (see module
        docstring). `sinks=None` (every other/existing caller, including
        this file's own tests) still gets a fresh dict, same as before.
        """
        sinks = {} if sinks is None else sinks
        if 'grades' in reports:
            sinks['grades_summary'] = _CsvSink(os.path.join(output_dir, 'grades_summary.csv'))
            sinks['grades_summary'].write_header(['course_id'] + GRADES_SUMMARY_COLUMNS)
        if 'profiles' in reports:
            constant_columns = [c for c in feature_list if c not in COURSE_VARYING_PROFILE_FEATURES]
            sinks['learner_profile'] = _CsvSink(os.path.join(output_dir, 'learner_profile.csv'))
            sinks['learner_profile'].write_header(constant_columns)
        if 'enrollments' in reports:
            course_varying_present = [c for c in COURSE_VARYING_PROFILE_FEATURES if c in feature_list]
            sinks['course_enrollments'] = _CsvSink(os.path.join(output_dir, 'course_enrollments.csv'))
            sinks['course_enrollments'].write_header(['course_id', 'id'] + course_varying_present)
        if 'problem_responses' in reports:
            sinks['problem_responses'] = _DictCsvSink(os.path.join(output_dir, 'problem_responses.csv'))
        if 'ora2' in reports:
            sinks['ora2_responses'] = _DictCsvSink(os.path.join(output_dir, 'ora2_responses.csv'))
        if 'may_enroll' in reports:
            sinks['may_enroll_info'] = _CsvSink(os.path.join(output_dir, 'may_enroll_info.csv'))
            sinks['may_enroll_info'].write_header(['course_id'] + list(MAY_ENROLL_DEFAULT_FEATURES))
        return sinks

    def _write_course_csv(self, per_course_dir, course_id, report_name, header, rows):
        """
        Write one course's untouched, tenant-filtered rows to
        per_course/<course_id>__<report>.csv (full column fidelity, no
        forced alignment across courses -- see module docstring).
        """
        path = os.path.join(per_course_dir, u"{0}__{1}.csv".format(_safe_course_id(course_id), report_name))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as output_file:
            writer = csv.writer(output_file, dialect='excel', quotechar='"', quoting=csv.QUOTE_ALL)
            # header is NOT always a fixed constant this command controls --
            # CourseGradeReport's header includes course-author-supplied
            # experiment-partition/assignment names -- so it gets the same
            # formula-injection escaping as the data rows (_escape_csv_formula).
            writer.writerow([_escape_csv_formula(value) for value in header])
            for row in rows:
                writer.writerow([_escape_csv_formula(value) for value in row])
        return len(rows)

    def _write_manifest(self, output_dir, manifest):
        """
        Write MANIFEST.json -- this command's own audit trail, replacing
        the InstructorTask trail a real Celery submission would have left.

        Called from handle()'s finally block inside a try/except. If this
        write fails while an original exception is already propagating
        (KeyboardInterrupt/MemoryError mid-run), the failure is logged and
        swallowed so it cannot replace that original exception -- the
        operator's only signal for why the run stopped. On an otherwise
        successful run there is nothing to mask, so the failure is raised
        as a CommandError instead: an export that reports success with no
        audit trail on disk is worse than a loud failure.
        """
        path = os.path.join(output_dir, MANIFEST_FILENAME)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as manifest_file:
            json.dump(manifest, manifest_file, indent=2, default=text_type)

    def _course_has_report_error(self, course_status):
        """
        True if `course_status` (one course's own entry in
        manifest['courses'] -- a dict of {report_name: per-report status
        dict}) has at least one report that lost something, for the
        purposes of handle()'s own manifest['courses_with_errors'] rollup
        (see module docstring).

        Two things count:
        - A report whose own `status` is `'error'`.
        - A nominally-`'success'` report that also recorded a secondary
          sub-artifact failure -- today only `_run_grades`'s
          `grades_errors_export_error` does this: that failure is
          deliberately NOT allowed to flip the report's own `status` (the
          primary grades data was written correctly, see _run_grades's own
          docstring), but a real per-course file (grades_errors.csv) was
          still lost, and the run-level rollup must not miss that.

        One thing deliberately does NOT count: `'skipped'` (today only
        `_run_ora2`'s unverifiable-identity-column outcome) -- that is a
        deliberate, already-documented abstention from writing an unsafe
        unfiltered file, not a failure to write something that should have
        succeeded.

        `--skip-course` entries (`{'_skipped': '--skip-course'}` -- a flat
        dict of strings, not per-report status dicts) are never actually
        processed and must not be misread as an error.
        """
        if '_skipped' in course_status:
            return False
        for report_status in course_status.values():
            if not isinstance(report_status, dict):
                continue
            if report_status.get('status') == 'error':
                return True
            if any(key.endswith('_export_error') for key in report_status):
                return True
        return False

    def _course_needs_review(self, course_status):
        """
        True if `course_status` has at least one report that's a plain
        `'success'` (or `'skipped'`) but still worth an operator's attention
        -- NOT counted by `_course_has_report_error` above, since nothing
        actually failed, but real data is still missing or suspect for the
        purposes of handle()'s own manifest['courses_needing_review']
        rollup (see module docstring). Deliberately kept SEPARATE from
        `courses_with_errors` (and does not affect `manifest['status']`,
        which stays 'complete' here) -- this is a weaker, "you may want to
        look at this" signal, not "something failed".

        Two things count:
        - Any report carrying its own `warning` key (`_empty_filter_warning`
          -- every one of that course's rows for that report was filtered
          out by the tenant-membership filter, which usually means the
          identity column didn't actually match rather than "this course
          genuinely has zero tenant rows").
        - A report whose `status` is `'skipped'` (today only `_run_ora2`'s
          unverifiable-identity-column outcome) -- a deliberate abstention,
          not a failure, but that course's ora2 data is still genuinely
          absent from this export and an operator deciding whether that's
          acceptable needs a top-level pointer to it, not just a per-course
          JSON entry nobody was told to go look for.

        `--skip-course` entries are never counted (never actually processed).
        """
        if '_skipped' in course_status:
            return False
        for report_status in course_status.values():
            if not isinstance(report_status, dict):
                continue
            if report_status.get('status') == 'skipped':
                return True
            if 'warning' in report_status:
                return True
        return False

    # ------------------------------------------------------------------ report invocation

    def _invoke_report(self, call_fn):
        """
        Call one of the five report-generator functions via the direct-call
        seam, returning ((header, rows), raw_result, error_result).
        `raw_result` is the function's own return value (a progress dict,
        or the 'failed' sentinel string for export_ora2_data) -- callers
        branch on it before trusting `(header, rows)`. `(header, rows)` is
        None if the call produced no captured upload (the ora2 internal-
        failure path, or anything unexpected).

        `error_result` surfaces any SEPARATE captured upload whose
        `csv_name` ends in `_err` -- as (error_header, error_rows), or None
        if no such upload was captured. Only CourseGradeReport._upload
        actually produces one of these today (the grade_report_err upload
        listing students CourseGradeFactory failed to grade,
        `tasks_helper/grades.py`); every other caller here will simply get
        `error_result=None` back. Previously the `_err`-suffixed upload was
        filtered out of `primary` and silently discarded entirely, along
        with the students it named -- see _run_grades for how it's used.
        """
        buffer = []
        with _celery_free_context(), _capture_csv_uploads(buffer):
            raw_result = call_fn()
        if raw_result == UPDATE_STATUS_FAILED:
            return None, raw_result, None

        error_result = None
        error_entries = [entry for entry in buffer if entry['csv_name'].endswith('_err')]
        if error_entries and error_entries[0]['rows']:
            error_rows_with_header = error_entries[0]['rows']
            error_result = (error_rows_with_header[0], error_rows_with_header[1:])

        primary = [entry for entry in buffer if not entry['csv_name'].endswith('_err')]
        if not primary:
            return None, raw_result, error_result
        rows = primary[0]['rows']
        if not rows:
            return ([], []), raw_result, error_result
        header, data_rows = rows[0], rows[1:]
        return (header, data_rows), raw_result, error_result

    def _filter_rows_by_column(self, header, rows, column_name, allowed_values):
        """
        Filter `rows` to those whose `column_name` value is in
        `allowed_values` -- the tenant-membership filter every sink applies
        (structural fix #1, see module docstring). Raises (caught by the
        per-course try/except in `_process_course`) if the expected identity
        column is missing -- refusing to silently ship an unfiltered export
        is safer than guessing.
        """
        if column_name not in header:
            raise CommandError(
                u"Expected identity column '{0}' not found in report header {1} -- refusing to "
                u"write an unfiltered tenant export.".format(column_name, header)
            )
        return self._filter_rows_by_index(rows, header.index(column_name), allowed_values)

    def _filter_rows_by_index(self, rows, idx, allowed_values):
        """
        Filter `rows` to those whose value at `idx` is in `allowed_values`,
        comparing as strings on both sides. Report values are NOT reliably
        the same Python type as the id/username sets this command resolves
        independently -- confirmed from source:
        `enrolled_students_features`'s `extract_attr`
        (`instructor_analytics/basic.py`) stringifies every profile feature
        it returns, including `id`, via a `DjangoJSONEncoder().default(attr)`
        call made directly (not through the normal encode path) -- the base
        `json.JSONEncoder.default()` unconditionally raises `TypeError` for
        a plain `int` (verified: `json.JSONEncoder().default(42)` raises),
        and `DjangoJSONEncoder` doesn't special-case `int`, so the `except`
        branch's `six.text_type(attr)` always fires, turning `student.id`
        into `'42'`, not `42`. Comparing raw types here would silently match
        nothing against `user_ids` (a set of real ints from `_get_user_ids`)
        for every profiles/enrollments row, every run. Stringifying both
        sides is safe everywhere else too: grades' `Student ID` is a real
        int on both sides and still matches after stringifying; ORA2 anon
        ids/usernames and problem_responses usernames are already strings.
        """
        allowed = {text_type(value) for value in allowed_values}
        return [row for row in rows if idx < len(row) and text_type(row[idx]) in allowed]

    def _empty_filter_warning(self, rows, filtered):
        """
        If tenant filtering zeroed out an otherwise non-empty report,
        surface that loudly (manifest + log) rather than reporting a quiet
        `'rows': 0` success. An all-rows-filtered-out result is far more
        likely to mean the identity column didn't actually match (wrong
        type, wrong column) than "this course genuinely has zero tenant
        rows" -- every course this command processes was resolved because
        at least one tenant member is enrolled in it or it matched the
        tenant's org filter (structural fix #2), so a total wipeout on a
        non-empty report is a signal worth an operator's attention, not a
        silent success.
        """
        if rows and not filtered:
            warning = u"all {0} row(s) were filtered out -- check the identity column".format(len(rows))
            logger.warning("export_tenant_reports_csv: %s", warning)
            return warning
        return None

    def _detect_ora2_identity_column(self, header, rows, course_id, override_column):
        """
        Verify an ora2 identity column BY CONTENT against two identity
        spaces this command controls, rather than guessing a column name
        (see module docstring's ora2 gap). Returns (column_index, label)
        where label is 'anonymous_user_id' or 'username', or (None, None)
        if no column qualifies. `override_column`, if given, bypasses
        detection entirely (an operator who has confirmed the real schema).
        """
        if override_column:
            if override_column not in header:
                raise CommandError(
                    u"--ora2-identity-column '{0}' not found in ora2 report header {1}.".format(
                        override_column, header
                    )
                )
            return header.index(override_column), 'override'

        if not rows:
            return None, None

        anon_superset = set(
            AnonymousUserId.objects.using(read_replica_or_default())
            .filter(course_id=course_id).values_list('anonymous_user_id', flat=True)
        )
        enrolled_user_ids = (
            CourseEnrollment.objects.using(read_replica_or_default())
            .filter(course_id=course_id).values_list('user_id', flat=True)
        )
        username_superset = set(
            get_user_model().objects.using(read_replica_or_default())
            .filter(id__in=enrolled_user_ids).values_list('username', flat=True)
        )
        candidates = [('anonymous_user_id', anon_superset), ('username', username_superset)]

        for col_idx in range(len(header)):
            values = [row[col_idx] for row in rows if col_idx < len(row) and row[col_idx] not in (None, '')]
            if not values:
                continue
            for label, value_set in candidates:
                if value_set and all(value in value_set for value in values):
                    return col_idx, label
        return None, None

    # ------------------------------------------------------------------ per-course orchestration

    def _process_course(self, course_id, reports, user_ids, tenant_usernames, feature_list, operator_user,
                         ora2_identity_column, per_course_dir, sinks, seen_learner_ids):
        """
        Run every requested report type against one course, isolated from
        every other course by the try/except inside each `_run_*` method --
        a broken/draft/zero-enrollment course must not abort a run covering
        many courses (e.g. CourseGradeReport._compile's `zip(*batched_rows)`
        raises ValueError for a zero-enrollment course, confirmed from
        source).
        """
        status = {}
        needed_keys = {GENERATOR_KEY_FOR_REPORT[r] for r in reports}

        if 'grades' in needed_keys:
            status['grades'] = self._run_grades(course_id, user_ids, per_course_dir, sinks.get('grades_summary'))

        if 'student_features' in needed_keys:
            want_profiles = 'profiles' in reports
            want_enrollments = 'enrollments' in reports
            profile_status = self._run_student_features(
                course_id, user_ids, feature_list, per_course_dir, want_profiles, want_enrollments,
                sinks.get('learner_profile'), sinks.get('course_enrollments'), seen_learner_ids,
            )
            if want_profiles:
                status['profiles'] = profile_status
            if want_enrollments:
                status['enrollments'] = profile_status

        if 'problem_responses' in needed_keys:
            status['problem_responses'] = self._run_problem_responses(
                course_id, tenant_usernames, operator_user, per_course_dir, sinks.get('problem_responses'),
            )

        if 'ora2' in needed_keys:
            status['ora2'] = self._run_ora2(
                course_id, user_ids, ora2_identity_column, per_course_dir, sinks.get('ora2_responses'),
            )

        if 'may_enroll' in needed_keys:
            status['may_enroll'] = self._run_may_enroll(course_id, per_course_dir, sinks.get('may_enroll_info'))

        return status

    def _run_grades(self, course_id, user_ids, per_course_dir, sink):
        """
        CourseGradeReport.generate -- identity column is 'Student ID'
        (`user.id`, confirmed from `_success_headers`/`_rows_for_users`).

        CourseGradeReport also uploads a SEPARATE grade_report_err CSV
        (`tasks_helper/grades.py:_upload`) listing every learner
        CourseGradeFactory failed to grade -- previously silently dropped
        by _invoke_report along with the failure count itself
        (`context.task_progress.failed`, discarded by this method's own
        `result, _ = ...`). Those error rows get the same tenant-membership
        filter as every other sink here (see module docstring's structural
        fix #1) and land in their own per-course file rather than vanishing.
        """
        try:
            result, raw_result, error_result = self._invoke_report(
                lambda: grades_module.CourseGradeReport.generate(None, None, course_id, {}, 'grades')
            )
            if result is None:
                return {'status': 'error', 'error': 'no upload captured'}
            header, rows = result
            if not header:
                return {'status': 'success', 'rows': 0}
            filtered = self._filter_rows_by_column(header, rows, 'Student ID', user_ids)
            warning = self._empty_filter_warning(rows, filtered)

            summary_rows = None
            if sink is not None:
                # Validate GRADES_SUMMARY_COLUMNS presence BEFORE writing anything --
                # a missing summary column must fail this course cleanly (caught
                # below) rather than after the per-course file has already landed.
                indices = [header.index(col) for col in GRADES_SUMMARY_COLUMNS]
                summary_rows = [[text_type(course_id)] + [row[idx] for idx in indices] for row in filtered]

            row_count = self._write_course_csv(per_course_dir, course_id, 'grades', header, filtered)
            if summary_rows is not None:
                sink.write_rows(summary_rows)

            status = {'status': 'success', 'rows': row_count}
            if warning:
                status['warning'] = warning

            if error_result is not None:
                error_header, error_rows = error_result
                # Safe to record up front -- no IO, and the operator needs this count
                # even if the error-row export below (filtering/writing
                # grades_errors.csv) fails. These two counts can legitimately differ:
                # 'failed' is CourseGradeReport's own course-wide count
                # (context.task_progress.failed, every learner it couldn't grade,
                # regardless of tenant); 'failed_rows_exported' (below) is how many of
                # those rows actually belong to THIS tenant -- a course shared with
                # another tenant can have failures that aren't this tenant's to see.
                if isinstance(raw_result, dict):
                    status['failed'] = raw_result.get('failed', len(error_rows))
                else:
                    status['failed'] = len(error_rows)
                try:
                    filtered_error_rows = self._filter_rows_by_column(
                        error_header, error_rows, 'Student ID', user_ids)
                    self._write_course_csv(
                        per_course_dir, course_id, 'grades_errors', error_header, filtered_error_rows)
                    status['failed_rows_exported'] = len(filtered_error_rows)
                except Exception as exc:  # pylint: disable=broad-except
                    # This runs AFTER the primary grades CSV/summary rows are already
                    # written to disk -- isolated in its own try/except so a failure here
                    # (writing grades_errors.csv, or the identity-column filter above)
                    # can't retroactively flip an otherwise-successful course to 'error'
                    # and misreport grades data that was actually written correctly.
                    logger.exception(
                        "export_tenant_reports_csv: grades error rows failed for %s", course_id
                    )
                    status['grades_errors_export_error'] = text_type(exc)

            return status
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("export_tenant_reports_csv: grades failed for %s", course_id)
            return {'status': 'error', 'error': text_type(exc)}

    def _run_student_features(self, course_id, user_ids, feature_list, per_course_dir, want_profiles,
                               want_enrollments, profile_sink, enrollment_sink, seen_learner_ids):
        """
        upload_students_csv -- task_input here is the bare feature list
        itself, not a dict (confirmed from instructor_task/api.py's
        submit_calculate_students_features_csv). Identity column is 'id'
        (forced into feature_list by `_resolve_include_fields`).
        """
        try:
            result, _, _ = self._invoke_report(
                lambda: enrollments_module.upload_students_csv(None, None, course_id, feature_list, 'features')
            )
            if result is None:
                return {'status': 'error', 'error': 'no upload captured'}
            header, rows = result
            if not header:
                return {'status': 'success', 'rows': 0}
            filtered = self._filter_rows_by_column(header, rows, 'id', user_ids)
            warning = self._empty_filter_warning(rows, filtered)

            if want_profiles:
                self._write_course_csv(per_course_dir, course_id, 'profiles', header, filtered)
            if want_enrollments:
                self._write_course_csv(per_course_dir, course_id, 'enrollments', header, filtered)

            id_idx = header.index('id')
            if want_profiles and profile_sink is not None:
                constant_columns = [c for c in header if c not in COURSE_VARYING_PROFILE_FEATURES]
                indices = [header.index(c) for c in constant_columns]
                new_rows = []
                for row in filtered:
                    learner_id = row[id_idx]
                    if learner_id in seen_learner_ids:
                        continue
                    seen_learner_ids.add(learner_id)
                    new_rows.append([row[idx] for idx in indices])
                profile_sink.write_rows(new_rows)

            if want_enrollments and enrollment_sink is not None:
                course_varying_present = [c for c in COURSE_VARYING_PROFILE_FEATURES if c in header]
                if course_varying_present:
                    indices = [header.index(c) for c in course_varying_present]
                    new_rows = [
                        [text_type(course_id), row[id_idx]] + [row[idx] for idx in indices]
                        for row in filtered
                    ]
                    enrollment_sink.write_rows(new_rows)

            status = {'status': 'success', 'rows': len(filtered)}
            if warning:
                status['warning'] = warning
            return status
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("export_tenant_reports_csv: student_features failed for %s", course_id)
            return {'status': 'error', 'error': text_type(exc)}

    def _run_problem_responses(self, course_id, tenant_usernames, operator_user, per_course_dir, sink):
        """
        ProblemResponses.generate -- task_input is a dict requiring
        'problem_locations' (the course root usage key, as a string) and
        'user_id' (the operator's id). Identity column is 'username'
        (confirmed from `_build_student_data`'s student_data_keys_list).
        """
        try:
            task_input = {
                'problem_locations': text_type(modulestore().make_course_usage_key(course_id)),
                'user_id': operator_user.id,
            }
            result, _, _ = self._invoke_report(
                lambda: grades_module.ProblemResponses.generate(None, None, course_id, task_input, 'responses')
            )
            if result is None:
                return {'status': 'error', 'error': 'no upload captured'}
            header, rows = result
            if not header:
                return {'status': 'success', 'rows': 0}
            filtered = self._filter_rows_by_column(header, rows, 'username', tenant_usernames)
            warning = self._empty_filter_warning(rows, filtered)
            row_count = self._write_course_csv(per_course_dir, course_id, 'problem_responses', header, filtered)
            if sink is not None:
                sink.add_rows(course_id, header, filtered)
            status = {'status': 'success', 'rows': row_count}
            if warning:
                status['warning'] = warning
            return status
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("export_tenant_reports_csv: problem_responses failed for %s", course_id)
            return {'status': 'error', 'error': text_type(exc)}

    def _run_ora2(self, course_id, user_ids, ora2_identity_column, per_course_dir, sink):
        """
        upload_ora2_data -- returns the plain string 'failed'
        (UPDATE_STATUS_FAILED) on internal failure instead of raising or
        returning an empty report. Identity column is detected by content,
        not name (see module docstring's ora2 gap / `_detect_ora2_identity_column`).
        """
        try:
            result, raw_result, _ = self._invoke_report(
                lambda: misc_module.upload_ora2_data(None, None, course_id, {}, 'ora2')
            )
            if raw_result == UPDATE_STATUS_FAILED:
                return {'status': 'error', 'error': 'upload_ora2_data returned the failure sentinel'}
            if result is None:
                return {'status': 'error', 'error': 'no upload captured'}
            header, rows = result
            if not header:
                return {'status': 'success', 'rows': 0}

            col_idx, label = self._detect_ora2_identity_column(header, rows, course_id, ora2_identity_column)
            if col_idx is None:
                logger.warning(
                    "export_tenant_reports_csv: could not verify an identity column in the ora2 report "
                    "for %s -- skipping to avoid writing an unfiltered cross-tenant export (see module "
                    "docstring's ora2 gap; pass --ora2-identity-column to override).",
                    course_id,
                )
                return {'status': 'skipped', 'reason': 'identity column unverified'}

            tenant_anon_ids = set(
                AnonymousUserId.objects.using(read_replica_or_default())
                .filter(course_id=course_id, user_id__in=user_ids)
                .values_list('anonymous_user_id', flat=True)
            )
            tenant_usernames = set(
                get_user_model().objects.using(read_replica_or_default())
                .filter(id__in=user_ids).values_list('username', flat=True)
            )
            if label == 'anonymous_user_id':
                tenant_values = tenant_anon_ids
            elif label == 'username':
                tenant_values = tenant_usernames
            else:
                # 'override' -- an operator-supplied column of unconfirmed shape (that's the
                # whole point of the override: detection couldn't verify it by content). Match
                # against the UNION of every tenant identity space this command knows, rather
                # than guessing which one the operator's column actually holds -- assuming it's
                # a raw user-id column (the previous, wrong behavior here) silently zeroed out
                # every override run whose column was actually an anon-id or username column.
                tenant_values = tenant_anon_ids | tenant_usernames | set(user_ids)

            filtered = self._filter_rows_by_index(rows, col_idx, tenant_values)
            warning = self._empty_filter_warning(rows, filtered)
            row_count = self._write_course_csv(per_course_dir, course_id, 'ora2', header, filtered)
            if sink is not None:
                sink.add_rows(course_id, header, filtered)
            status = {'status': 'success', 'rows': row_count, 'identity_column': header[col_idx]}
            if warning:
                status['warning'] = warning
            return status
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("export_tenant_reports_csv: ora2 failed for %s", course_id)
            return {'status': 'error', 'error': text_type(exc)}

    def _run_may_enroll(self, course_id, per_course_dir, sink):
        """
        upload_may_enroll_csv -- NOT tenant-membership-filtered by design
        (CourseEnrollmentAllowed rows have no user account, see module
        docstring); only reached when explicitly requested via --reports.
        """
        try:
            result, _, _ = self._invoke_report(
                lambda: enrollments_module.upload_may_enroll_csv(
                    None, None, course_id, {'features': list(MAY_ENROLL_DEFAULT_FEATURES)}, 'may_enroll'
                )
            )
            if result is None:
                return {'status': 'error', 'error': 'no upload captured'}
            header, rows = result
            if not header:
                return {'status': 'success', 'rows': 0, 'caveat': 'not tenant-membership-filtered'}
            row_count = self._write_course_csv(per_course_dir, course_id, 'may_enroll', header, rows)
            if sink is not None:
                summary_rows = [[text_type(course_id)] + row for row in rows]
                sink.write_rows(summary_rows)
            return {'status': 'success', 'rows': row_count, 'caveat': 'not tenant-membership-filtered'}
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("export_tenant_reports_csv: may_enroll failed for %s", course_id)
            return {'status': 'error', 'error': text_type(exc)}

    # ------------------------------------------------------------------ dry run / reporting

    def _dry_run(self, course_ids, reports):
        """
        Print the resolved (course x report-type) matrix without generating anything.
        """
        self.stdout.write(u"\nCourse x Report matrix ({0} courses):".format(len(course_ids)))
        for course_id in sorted(course_ids, key=text_type):
            self.stdout.write(u"  {0}: {1}".format(course_id, ', '.join(reports)))
        self.stdout.write(self.style.WARNING("\nDRY RUN -- no files written, no reports generated."))

    def _print_header(self, slug, reports, dry_run):
        """
        Print the command's run header.
        """
        self.stdout.write("\n" + "=" * 72)
        self.stdout.write(u"Export tenant CSV reports: {0}".format(slug) + ("  [DRY RUN]" if dry_run else ""))
        self.stdout.write(u"Reports: {0}".format(', '.join(reports)))
        self.stdout.write("=" * 72)
