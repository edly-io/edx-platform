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
  cap by default) is temporarily overridden to `None` for the duration of
  any run that requests this report -- see `_no_max_problem_responses_limit`
  -- with a loud log line, then restored.
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

Known limit inherited from the reused generator code, not fixable here:
`CourseGradeReport._generate` materializes a full course's enrollment batch
in memory regardless of caller. For a tenant with one very large course,
this is a real per-course memory ceiling this command's own streaming
discipline cannot fully paper over.

The `may_enroll_info` gap
============================
`may_enroll_info` reports on `CourseEnrollmentAllowed` rows: pending,
not-yet-registered invitees identified only by email, with no user account
and therefore no `EdlyMultiSiteAccess` row to filter against by definition.
It is excluded from the default `--reports` list; requesting it via
`--reports may_enroll` produces a file covering *every* pending invite for
the resolved courses, not just this tenant's -- this caveat is recorded in
the manifest and printed as a warning at runtime, not just documented here.

The ora2 identity-column gap -- genuinely unverified, resolved by a content
check, not a name guess
=============================================================================
`openassessment`/`edx-ora2` is pinned in `requirements/edx/base.txt` as an
editable VCS install (`edly-io/edx-ora2@develop-koa`) and is NOT vendored
in the checkout this command was authored against -- confirmed by search,
not assumed -- so `OraAggregateData.collect_ora2_data`'s exact column
layout could not be read directly. Rather than guess a column name (which
would either silently under-filter -- a cross-tenant PII leak, since a
course can have members of more than one tenant -- or silently
over-filter), `_detect_ora2_identity_column` verifies a column *by its
actual content* against two known identity spaces this command DOES
control: every `student_anonymoususerid.anonymous_user_id` and every
enrolled username for that course. A column qualifies only if every
non-empty value in it is contained in one of those sets. If detection
finds no qualifying column, that course's ora2 output is skipped entirely
(not written unfiltered) and recorded in the manifest as
`skipped: identity column unverified`, with a loud log line pointing at
`--ora2-identity-column` as the override for an operator who has confirmed
the real schema in a real devstack. This must be verified against a real
course fixture before this ships to production use without the override.

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
# varies by course and stays in the per-course file only).
GRADES_SUMMARY_COLUMNS = [
    'Student ID', 'Email', 'Username', 'Enrollment Track', 'Verification Status',
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
def _no_max_problem_responses_limit():
    """
    Temporarily lift the FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] cap (a
    hard 5000-responses-per-course default) for the duration of a run that
    requests problem_responses -- otherwise this backup silently truncates
    at 5000 responses per course. Restores the original value afterward
    even if the run raises.
    """
    original = settings.FEATURES.get('MAX_PROBLEM_RESPONSES_COUNT')
    logger.warning(
        "export_tenant_reports_csv: temporarily overriding "
        "FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] to None for this run (was %r) -- "
        "problem_responses defaults to a hard 5000-response-per-course cap, which "
        "would silently truncate this off-boarding export otherwise.",
        original,
    )
    settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] = None
    try:
        yield
    finally:
        settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] = original


@contextmanager
def _null_context():
    """
    No-op context manager for when problem_responses isn't requested and
    `_no_max_problem_responses_limit` shouldn't run at all.
    """
    yield


def _safe_course_id(course_id):
    """
    Sanitize a course id for use in a filename -- same characters
    `ProblemResponses._generate_upload_file_name` strips.
    """
    return re.sub(r'[:/+]', '_', text_type(course_id))


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
            self._writer.writerow(header)
            self._header_written = True

    def write_rows(self, rows):
        for row in rows:
            self._writer.writerow(row)
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
            writer.writeheader()
            for entry in self._rows:
                writer.writerow(entry)


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
        if 'problem_responses' in reports and operator_user is None:
            raise CommandError("--as-user is required when --reports includes problem_responses.")

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
            get_user_model().objects.filter(id__in=user_ids).values_list('username', flat=True)
        )
        feature_list = self._build_feature_list(include_fields, reports)

        sinks = self._open_sinks(output_dir, reports, feature_list)

        manifest = {
            'slug': slug,
            'operator': operator_user.username if operator_user else getpass.getuser(),
            'generated_at': timezone.now().isoformat(),
            'reports_requested': list(reports),
            'overrides': {
                'include_fields': include_fields,
                'allow_meta_field': bool(options.get('allow_meta_field')),
                'max_problem_responses_count_override': 'problem_responses' in reports,
                'ora2_identity_column_override': ora2_identity_column,
            },
            'courses': {},
            'summary_files': {},
        }
        if 'ora2' in reports:
            manifest['known_gaps'] = {
                'ora2_schema_unverified': (
                    "OraAggregateData.collect_ora2_data's exact column layout was not vendored/"
                    "importable in the environment this command was authored in. The identity column "
                    "is detected at runtime by content, not assumed by name -- courses where detection "
                    "fails are recorded below as 'skipped: identity column unverified', not silently "
                    "included unfiltered. Verify against a real course fixture before relying on this "
                    "without --ora2-identity-column."
                ),
            }

        seen_learner_ids = set()
        max_problem_responses_ctx = (
            _no_max_problem_responses_limit() if 'problem_responses' in reports else _null_context()
        )

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

        for name, sink in sinks.items():
            sink.close()
            manifest['summary_files'][name + '.csv'] = sink.row_count

        self._write_manifest(output_dir, manifest)

        self.stdout.write("\n" + "=" * 72)
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
            return EdlySubOrganization.objects.get(slug=slug)
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
        return set(EdlyMultiSiteAccess.objects.filter(sub_org=sub_org).values_list('user_id', flat=True))

    def _get_tenant_course_ids(self, orgs, user_ids):
        """
        Amended course-set union (structural fix #2, see module docstring):
        org-filtered courses alone miss a member's enrollments in courses
        outside the tenant's own org list. Returns real CourseKey objects.
        """
        org_course_ids = set(CourseOverview.objects.filter(org__in=orgs).values_list('id', flat=True))
        member_course_ids = set(
            CourseEnrollment.objects.filter(user_id__in=user_ids, is_active=True)
            .values_list('course_id', flat=True).distinct()
        )
        return org_course_ids | member_course_ids

    def _resolve_operator(self, identifier):
        """
        Resolve --as-user by username or email.
        """
        User = get_user_model()
        try:
            return User.objects.get(Q(username=identifier) | Q(email=identifier))
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
        responses).
        """
        if not os.path.exists(path):
            os.makedirs(path, mode=0o700)
        os.chmod(path, 0o700)

    def _open_sinks(self, output_dir, reports, feature_list):
        """
        Open the tenant-wide summary sinks for the requested reports and
        write their headers up front where the header is a fixed constant
        (see module docstring: _CsvSink vs _DictCsvSink).
        """
        sinks = {}
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
            writer.writerow(header)
            for row in rows:
                writer.writerow(row)
        return len(rows)

    def _write_manifest(self, output_dir, manifest):
        """
        Write MANIFEST.json -- this command's own audit trail, replacing
        the InstructorTask trail a real Celery submission would have left.
        """
        path = os.path.join(output_dir, MANIFEST_FILENAME)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as manifest_file:
            json.dump(manifest, manifest_file, indent=2, default=text_type)

    # ------------------------------------------------------------------ report invocation

    def _invoke_report(self, call_fn):
        """
        Call one of the five report-generator functions via the direct-call
        seam, returning ((header, rows), raw_result). `raw_result` is the
        function's own return value (a progress dict, or the 'failed'
        sentinel string for export_ora2_data) -- callers branch on it before
        trusting `(header, rows)`. Returns (None, raw_result) if the call
        produced no captured upload (the ora2 internal-failure path, or
        anything unexpected).
        """
        buffer = []
        with _celery_free_context(), _capture_csv_uploads(buffer):
            raw_result = call_fn()
        if raw_result == UPDATE_STATUS_FAILED:
            return None, raw_result
        primary = [entry for entry in buffer if not entry['csv_name'].endswith('_err')]
        if not primary:
            return None, raw_result
        rows = primary[0]['rows']
        if not rows:
            return ([], []), raw_result
        header, data_rows = rows[0], rows[1:]
        return (header, data_rows), raw_result

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
            AnonymousUserId.objects.filter(course_id=course_id).values_list('anonymous_user_id', flat=True)
        )
        enrolled_user_ids = CourseEnrollment.objects.filter(course_id=course_id).values_list('user_id', flat=True)
        username_superset = set(
            get_user_model().objects.filter(id__in=enrolled_user_ids).values_list('username', flat=True)
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
        """
        try:
            result, _ = self._invoke_report(
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
            result, _ = self._invoke_report(
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
            result, _ = self._invoke_report(
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
            result, raw_result = self._invoke_report(
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
                AnonymousUserId.objects.filter(course_id=course_id, user_id__in=user_ids)
                .values_list('anonymous_user_id', flat=True)
            )
            tenant_usernames = set(
                get_user_model().objects.filter(id__in=user_ids).values_list('username', flat=True)
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
            result, _ = self._invoke_report(
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
