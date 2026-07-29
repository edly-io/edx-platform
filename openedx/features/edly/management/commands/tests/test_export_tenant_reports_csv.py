"""
Tests for the export_tenant_reports_csv management command.

Tenant/course/user resolution is exercised against the real ORM (sqlite in
test settings handles these fine, matching the pattern used throughout
openedx/features/edly/tests/ and test_export_learner_data.py). Report
invocation is exercised via the exact patch pattern test_tasks_helper.py
already uses to call these functions outside Celery
(`runner._get_current_task`, per-module `upload_csv_to_report_store`) --
the fake report-generator functions below call that same real, unpatched
`_capture_csv_uploads`/`_celery_free_context` machinery, so what's under
test is this command's own capture/filter/merge/dedupe/manifest logic, not
a re-implementation of it.

Known gap: none of the tests below exercise the real
`CourseGradeReport`/`ProblemResponses`/`upload_students_csv`/
`upload_may_enroll_csv`/`upload_ora2_data` functions against a real course
fixture (ModuleStoreTestCase/TestReportMixin/InstructorTaskCourseTestCase,
the "gold standard" per this command's module docstring) -- that requires
a full devstack run, not exercised in the environment this file was
authored in. In particular the `problem_locations` course-root-usage-key
walk and the real `OraAggregateData.collect_ora2_data` column layout are
NOT verified end-to-end here; see the module docstring's own notes on both.

`HandleTests` and `ReadReplicaRoutingTests` (added alongside the fixes
above) follow the same real-ORM/real-`call_command` pattern as the rest of
this file -- they were authored and reviewed against real source, but like
every other TestCase here, actually running them requires a real Django
test environment (sqlite test settings + migrations) that was not
available in the environment they were authored in either; see this
command's own module docstring for the read-replica note these tests cover.
"""
import csv
import json
import os
import stat
import tempfile
from collections import OrderedDict
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from mock import MagicMock, patch

from common.djangoapps.student.models import AnonymousUserId
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.features.edly.management.commands import export_tenant_reports_csv as mod
from openedx.features.edly.management.commands.export_tenant_reports_csv import (
    COURSE_VARYING_PROFILE_FEATURES,
    DEFAULT_PROFILE_FEATURES,
    DEFAULT_REPORTS,
    GRADES_SUMMARY_COLUMNS,
    REPORT_CHOICES,
    Command,
    _CsvSink,
    _DictCsvSink,
)
from openedx.features.edly.tests.factories import EdlyMultiSiteAccessFactory, EdlySubOrganizationFactory
from student.tests.factories import CourseEnrollmentFactory, UserFactory


def _make_command():
    """
    Build a Command instance with stdout/style wired up for assertions,
    same convention as test_export_learner_data.py's `_make_command`.
    """
    command = Command()
    command.stdout = StringIO()
    command.stderr = StringIO()
    command.style = MagicMock()
    for attr in ('SUCCESS', 'ERROR', 'WARNING'):
        setattr(command.style, attr, lambda value: value)
    return command


class PatchTargetsRegressionTests(TestCase):
    """
    Regression test asserting the direct-call seam this command relies on
    still exists (see module docstring's "Execution mechanism" section):
    a future upstream refactor of tasks_helper/ should fail loudly here,
    not silently break this command.
    """

    def test_get_current_task_patch_seam_exists(self):
        assert hasattr(mod.runner_module, '_get_current_task')

    def test_upload_csv_to_report_store_exists_on_all_three_modules(self):
        assert hasattr(mod.grades_module, 'upload_csv_to_report_store')
        assert hasattr(mod.enrollments_module, 'upload_csv_to_report_store')
        assert hasattr(mod.misc_module, 'upload_csv_to_report_store')

    def test_five_report_generator_entry_points_exist(self):
        assert hasattr(mod.grades_module, 'CourseGradeReport')
        assert hasattr(mod.grades_module.CourseGradeReport, 'generate')
        assert hasattr(mod.grades_module, 'ProblemResponses')
        assert hasattr(mod.grades_module.ProblemResponses, 'generate')
        assert hasattr(mod.enrollments_module, 'upload_students_csv')
        assert hasattr(mod.enrollments_module, 'upload_may_enroll_csv')
        assert hasattr(mod.misc_module, 'upload_ora2_data')

    def test_update_status_failed_sentinel_unchanged(self):
        # upload_ora2_data returns this exact string on internal failure --
        # see module docstring / _run_ora2.
        assert mod.UPDATE_STATUS_FAILED == 'failed'


class ParseReportsTests(TestCase):
    """
    Tests for Command._parse_reports.
    """

    def test_default_reports_string_parses_to_the_documented_list(self):
        command = _make_command()
        assert command._parse_reports(','.join(DEFAULT_REPORTS)) == list(DEFAULT_REPORTS)

    def test_may_enroll_not_in_default_reports(self):
        # Structural: may_enroll can never be tenant-membership-filtered
        # (see module docstring) -- must stay opt-in only.
        assert 'may_enroll' not in DEFAULT_REPORTS
        assert 'may_enroll' in REPORT_CHOICES

    def test_unknown_report_type_raises_command_error(self):
        command = _make_command()
        with self.assertRaises(CommandError):
            command._parse_reports('grades,not-a-real-report')

    def test_whitespace_and_empty_entries_are_tolerated(self):
        command = _make_command()
        assert command._parse_reports(' grades , profiles ,,') == ['grades', 'profiles']


class ResolveIncludeFieldsTests(TestCase):
    """
    Tests for Command._resolve_include_fields -- the 'meta' secrets guard
    and the forced 'id' column (needed for membership filtering + the
    learner_profile.csv dedup key).
    """

    def test_default_matches_documented_profile_feature_list(self):
        command = _make_command()
        assert command._resolve_include_fields(None, False) == list(DEFAULT_PROFILE_FEATURES)

    def test_meta_blocked_without_allow_meta_field(self):
        command = _make_command()
        with self.assertRaises(CommandError):
            command._resolve_include_fields('id,meta', False)

    def test_meta_allowed_with_allow_meta_field(self):
        command = _make_command()
        fields = command._resolve_include_fields('id,meta', True)
        assert 'meta' in fields

    def test_id_forced_even_when_caller_omits_it(self):
        command = _make_command()
        fields = command._resolve_include_fields('username,email', False)
        assert 'id' in fields


class BuildFeatureListTests(TestCase):
    """
    Tests for Command._build_feature_list -- the course-varying columns
    are only requested when 'enrollments' is actually in --reports.
    """

    def test_adds_course_varying_columns_when_enrollments_requested(self):
        command = _make_command()
        features = command._build_feature_list(['id', 'username'], ['profiles', 'enrollments'])
        for column in COURSE_VARYING_PROFILE_FEATURES:
            assert column in features

    def test_omits_course_varying_columns_when_enrollments_not_requested(self):
        command = _make_command()
        features = command._build_feature_list(['id', 'username'], ['profiles'])
        for column in COURSE_VARYING_PROFILE_FEATURES:
            assert column not in features


class FilterRowsByColumnTests(TestCase):
    """
    Tests for Command._filter_rows_by_column -- the shared tenant-membership
    filter every report sink applies (structural fix #1, see module docstring).
    """

    def test_filters_rows_to_allowed_values(self):
        command = _make_command()
        header = ['Student ID', 'Email', 'Username']
        rows = [[1, 'a@x.com', 'alice'], [2, 'b@x.com', 'bob'], [3, 'c@x.com', 'carol']]
        filtered = command._filter_rows_by_column(header, rows, 'Student ID', {1, 3})
        assert filtered == [[1, 'a@x.com', 'alice'], [3, 'c@x.com', 'carol']]

    def test_missing_identity_column_raises_rather_than_shipping_unfiltered(self):
        command = _make_command()
        with self.assertRaises(CommandError):
            command._filter_rows_by_column(['x'], [['y']], 'Student ID', {1})


class GetTenantCourseIdsTests(TestCase):
    """
    Tests for Command._get_tenant_course_ids -- the amended course-set
    union (structural fix #2, see module docstring). This is the single
    scoping regression this command exists to not repeat.
    """

    def test_union_includes_org_filtered_and_member_enrolled_courses(self):
        command = _make_command()
        org_course = CourseOverviewFactory(org='tenant-org')
        outside_course = CourseOverviewFactory(org='other-org')
        member = UserFactory()
        CourseEnrollmentFactory(user=member, course_id=outside_course.id, is_active=True)

        course_ids = command._get_tenant_course_ids(['tenant-org'], {member.id})

        # The whole point of the union: a member's enrollment in a course
        # OUTSIDE the tenant's resolved orgs must still surface here --
        # org-filtering alone would silently miss it.
        assert org_course.id in course_ids
        assert outside_course.id in course_ids

    def test_inactive_enrollment_not_included_via_member_path(self):
        command = _make_command()
        course = CourseOverviewFactory(org='other-org')
        member = UserFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=False)

        course_ids = command._get_tenant_course_ids([], {member.id})

        assert course.id not in course_ids

    def test_returns_real_course_key_objects_not_strings(self):
        # The five report generators require actual CourseKey instances
        # (confirmed from test_tasks_helper.py -- every call there passes
        # self.course.id, not str(self.course.id)) -- unlike
        # export_learner_data.py's _get_course_ids, which stringifies for
        # JSON/display purposes only.
        command = _make_command()
        course = CourseOverviewFactory(org='tenant-org')
        course_ids = command._get_tenant_course_ids(['tenant-org'], set())
        assert course.id in course_ids
        assert not any(isinstance(cid, str) for cid in course_ids)


class GetSubOrgAndUserIdsTests(TestCase):
    """
    Sanity checks that this command's copies of _get_sub_org/_get_user_ids
    (reused verbatim from export_learner_data.py's pattern, see module
    docstring) behave identically.
    """

    def test_get_sub_org_not_found_raises_command_error(self):
        command = _make_command()
        with self.assertRaises(CommandError):
            command._get_sub_org('does-not-exist')

    def test_get_user_ids_uses_multisite_access_membership_only(self):
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        enrolled_only_user = UserFactory()
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=enrolled_only_user, course_id=course.id)

        command = _make_command()
        user_ids = command._get_user_ids(sub_org)

        assert user_ids == {member.id}


class DetectOra2IdentityColumnTests(TestCase):
    """
    Tests for Command._detect_ora2_identity_column -- content-based
    detection against real AnonymousUserId/CourseEnrollment/User rows (see
    module docstring's ora2 gap: this command does NOT guess a column name).
    """

    def test_detects_anonymous_user_id_column_by_content(self):
        command = _make_command()
        course = CourseOverviewFactory()
        user = UserFactory()
        AnonymousUserId.objects.create(user=user, course_id=course.id, anonymous_user_id='a' * 32)

        header = ['Submission ID', 'Anon Col', 'Response Text']
        rows = [['sub-1', 'a' * 32, 'The answer is 42']]

        col_idx, label = command._detect_ora2_identity_column(header, rows, course.id, None)

        assert (col_idx, label) == (1, 'anonymous_user_id')

    def test_detects_username_column_by_content_when_no_anon_id_match(self):
        command = _make_command()
        course = CourseOverviewFactory()
        user = UserFactory(username='scorer_1')
        CourseEnrollmentFactory(user=user, course_id=course.id)

        header = ['Submission ID', 'Scorer', 'Response Text']
        rows = [['sub-1', 'scorer_1', 'looks good']]

        col_idx, label = command._detect_ora2_identity_column(header, rows, course.id, None)

        assert (col_idx, label) == (1, 'username')

    def test_no_qualifying_column_returns_none_none(self):
        command = _make_command()
        course = CourseOverviewFactory()

        header = ['Submission ID', 'Response Text']
        rows = [['sub-1', 'free text nobody can be identified by']]

        col_idx, label = command._detect_ora2_identity_column(header, rows, course.id, None)

        assert (col_idx, label) == (None, None)

    def test_override_column_bypasses_detection(self):
        command = _make_command()
        course = CourseOverviewFactory()
        header = ['Submission ID', 'Whatever Col', 'Response Text']
        rows = [['sub-1', 'anything', 'text']]

        col_idx, label = command._detect_ora2_identity_column(header, rows, course.id, 'Whatever Col')

        assert (col_idx, label) == (1, 'override')

    def test_override_column_missing_from_header_raises(self):
        command = _make_command()
        course = CourseOverviewFactory()
        with self.assertRaises(CommandError):
            command._detect_ora2_identity_column(['a', 'b'], [['1', '2']], course.id, 'not-a-column')


class CsvSinkTests(TestCase):
    """
    Tests for _CsvSink -- the streaming tenant-wide sink for fixed-header
    reports (grades_summary/learner_profile/course_enrollments/may_enroll_info).
    """

    def test_writes_header_once_and_streams_rows(self):
        path = os.path.join(tempfile.mkdtemp(), 'sink.csv')
        sink = _CsvSink(path)
        sink.write_header(['course_id', 'Student ID'])
        sink.write_header(['ignored', 'second', 'call'])  # must be a no-op
        sink.write_rows([['course-1', 1], ['course-1', 2]])
        sink.close()

        with open(path) as f:
            rows = list(csv.reader(f))
        assert rows == [['course_id', 'Student ID'], ['course-1', '1'], ['course-1', '2']]
        assert sink.row_count == 2

    def test_escapes_csv_formula_injection_in_header_and_data(self):
        """
        Item 6 regression: QUOTE_ALL alone does not stop a spreadsheet app
        from evaluating a cell starting with =, +, -, or @ as a formula.
        """
        path = os.path.join(tempfile.mkdtemp(), 'sink.csv')
        sink = _CsvSink(path)
        sink.write_header(['name', '=cmd(1,1)'])
        sink.write_rows([['=cmd(1,1)'], ['+1+1'], ['-1+1'], ['@sum(1,1)'], ['alice']])
        sink.close()

        with open(path) as f:
            rows = list(csv.reader(f))
        assert rows == [
            ['name', "'=cmd(1,1)"],
            ["'=cmd(1,1)"],
            ["'+1+1"],
            ["'-1+1"],
            ["'@sum(1,1)"],
            ['alice'],
        ]


class DictCsvSinkTests(TestCase):
    """
    Tests for _DictCsvSink -- the buffered tenant-wide sink for reports
    whose per-course header is not a predetermined constant
    (problem_responses/ora2, see module docstring).
    """

    def test_unions_fieldnames_across_courses_with_differing_headers(self):
        path = os.path.join(tempfile.mkdtemp(), 'sink.csv')
        sink = _DictCsvSink(path)
        sink.add_rows('course-1', ['username', 'state'], [['alice', 's1']])
        sink.add_rows('course-2', ['username', 'state', 'extra_col'], [['bob', 's2', 'xyz']])
        sink.close()

        with open(path) as f:
            rows = list(csv.DictReader(f))
        assert set(rows[0].keys()) == {'course_id', 'username', 'state', 'extra_col'}
        assert rows[0]['extra_col'] == ''  # course-1 predates that column
        assert rows[1]['course_id'] == 'course-2'
        assert rows[1]['extra_col'] == 'xyz'
        assert sink.row_count == 2

    def test_escapes_csv_formula_injection_in_header_and_data(self):
        """
        Item 6 regression, _DictCsvSink variant -- its fieldnames come from
        each course's own report header (xblock/ora2 column names), not a
        fixed constant, so the header needs the same escaping as the data.
        """
        path = os.path.join(tempfile.mkdtemp(), 'sink.csv')
        sink = _DictCsvSink(path)
        sink.add_rows('course-1', ['=malicious_header', 'response'], [['x', '=cmd(1,1)']])
        sink.close()

        with open(path) as f:
            reader = csv.reader(f)
            header_row = next(reader)
            data_row = next(reader)
        assert header_row == ['course_id', "'=malicious_header", 'response']
        assert data_row[2] == "'=cmd(1,1)"


class EscapeCsvFormulaTests(TestCase):
    """
    Tests for the module-level _escape_csv_formula helper (item 6).
    """

    def test_prefixes_values_starting_with_formula_trigger_characters(self):
        for trigger in ('=', '+', '-', '@'):
            value = trigger + 'cmd(1,1)'
            assert mod._escape_csv_formula(value) == "'" + value

    def test_leaves_ordinary_strings_untouched(self):
        assert mod._escape_csv_formula('alice') == 'alice'
        assert mod._escape_csv_formula('') == ''

    def test_leaves_non_string_values_untouched(self):
        assert mod._escape_csv_formula(42) == 42
        assert mod._escape_csv_formula(None) is None
        assert mod._escape_csv_formula(True) is True


class WriteCourseCsvTests(TestCase):
    """
    Tests for Command._write_course_csv -- the per-course raw-writer path
    (full column fidelity, no forced alignment across courses, see module
    docstring), including its formula-injection escaping (item 6).
    """

    def test_writes_header_and_rows_verbatim(self):
        command = _make_command()
        per_course_dir = tempfile.mkdtemp()
        row_count = command._write_course_csv(
            per_course_dir, 'course-1', 'grades', ['Student ID', 'Username'], [['1', 'alice'], ['2', 'bob']],
        )
        assert row_count == 2
        with open(os.path.join(per_course_dir, 'course-1__grades.csv')) as f:
            rows = list(csv.reader(f))
        assert rows == [['Student ID', 'Username'], ['1', 'alice'], ['2', 'bob']]

    def test_escapes_csv_formula_injection_in_header_and_data(self):
        # CourseGradeReport's real header can include course-author-supplied
        # experiment-partition/assignment names -- not always a fixed constant.
        command = _make_command()
        per_course_dir = tempfile.mkdtemp()
        command._write_course_csv(
            per_course_dir, 'course-1', 'grades', ['name', '=Experiment Group (evil)'], [['=cmd(1,1)', 'x']],
        )
        with open(os.path.join(per_course_dir, 'course-1__grades.csv')) as f:
            rows = list(csv.reader(f))
        assert rows == [['name', "'=Experiment Group (evil)"], ["'=cmd(1,1)", 'x']]


class InvokeReportAndRunGradesTests(TestCase):
    """
    Tests exercising Command._invoke_report / _run_grades through the REAL
    _celery_free_context/_capture_csv_uploads machinery -- the fake
    `CourseGradeReport.generate` below calls the same
    `upload_csv_to_report_store([header] + rows, ...)` shape the real
    function calls (confirmed from tasks_helper/grades.py), so this proves
    the capture/celery-free plumbing itself, not a re-implementation of it.
    """

    def test_invoke_report_captures_rows_and_filters_to_tenant(self):
        command = _make_command()

        def fake_generate(_a, _b, course_id, _task_input, _action_name):
            header = ['Student ID', 'Email', 'Username', 'Enrollment Status']
            rows = [[1, 'a@x.com', 'alice', 'enrolled'], [99, 'z@x.com', 'zeke', 'enrolled']]
            mod.grades_module.upload_csv_to_report_store([header] + rows, 'grade_report', course_id, None)
            return {'succeeded': 2}

        with patch.object(mod.grades_module.CourseGradeReport, 'generate', staticmethod(fake_generate)):
            per_course_dir = tempfile.mkdtemp()
            status = command._run_grades('course-1', {1}, per_course_dir, None)

        assert status == {'status': 'success', 'rows': 1}
        with open(os.path.join(per_course_dir, 'course-1__grades.csv')) as f:
            written = list(csv.reader(f))
        assert written == [
            ['Student ID', 'Email', 'Username', 'Enrollment Status'],
            ['1', 'a@x.com', 'alice', 'enrolled'],
        ]

    def test_run_grades_writes_tenant_wide_summary_with_fixed_columns(self):
        command = _make_command()

        def fake_generate(_a, _b, course_id, _task_input, _action_name):
            header = ['Student ID', 'Email', 'Username'] + GRADES_SUMMARY_COLUMNS[3:]
            rows = [[1, 'a@x.com', 'alice'] + ['0.9', 'honor', 'N/A', 'N', 'N', '', 'enrolled']]
            mod.grades_module.upload_csv_to_report_store([header] + rows, 'grade_report', course_id, None)
            return {'succeeded': 1}

        with patch.object(mod.grades_module.CourseGradeReport, 'generate', staticmethod(fake_generate)):
            per_course_dir = tempfile.mkdtemp()
            sink_path = os.path.join(tempfile.mkdtemp(), 'grades_summary.csv')
            sink = _CsvSink(sink_path)
            sink.write_header(['course_id'] + GRADES_SUMMARY_COLUMNS)
            status = command._run_grades('course-1', {1}, per_course_dir, sink)
            sink.close()

        assert status['status'] == 'success'
        with open(sink_path) as f:
            rows = list(csv.DictReader(f))
        assert rows[0]['course_id'] == 'course-1'
        assert rows[0]['Student ID'] == '1'
        # Item 1 regression: grades_summary.csv previously had no grade in it at all
        # (GRADES_SUMMARY_COLUMNS omitted 'Grade' even though CourseGradeReport's real
        # header always emits it right after the identity columns).
        assert 'Grade' in rows[0]
        assert rows[0]['Grade'] == '0.9'

    def test_zero_enrollment_value_error_is_isolated_not_raised(self):
        """
        CourseGradeReport._compile's `zip(*batched_rows)` raises ValueError
        for a zero-enrollment course (confirmed from source: `zip()` with
        no arguments unpacked into two names raises). One broken/empty
        course must not abort a run covering many courses.
        """
        command = _make_command()

        def raising_generate(*args, **kwargs):
            raise ValueError("not enough values to unpack (expected 2, got 0)")

        with patch.object(mod.grades_module.CourseGradeReport, 'generate', staticmethod(raising_generate)):
            per_course_dir = tempfile.mkdtemp()
            status = command._run_grades('course-empty', {1}, per_course_dir, None)

        assert status['status'] == 'error'
        assert 'not enough values' in status['error']

    def test_missing_summary_column_fails_before_writing_the_per_course_file(self):
        """
        Regression test: GRADES_SUMMARY_COLUMNS presence must be validated
        BEFORE the per-course file is written, not after -- otherwise a
        missing summary column marks the course 'error' in the manifest
        while a per-course file sits on disk claiming otherwise.
        """
        command = _make_command()

        def fake_generate_missing_column(_a, _b, course_id, _task_input, _action_name):
            # Deliberately omits 'Certificate Type', one of GRADES_SUMMARY_COLUMNS --
            # 'Grade' IS present so this test isolates only the missing-'Certificate
            # Type' case, not a (separate, already-covered) missing-'Grade' case.
            header = [
                'Student ID', 'Email', 'Username', 'Grade', 'Enrollment Track', 'Verification Status',
                'Certificate Eligible', 'Certificate Delivered', 'Enrollment Status',
            ]
            rows = [[1, 'a@x.com', 'alice', '0.9', 'honor', 'N/A', 'N', 'N', 'enrolled']]
            mod.grades_module.upload_csv_to_report_store([header] + rows, 'grade_report', course_id, None)
            return {'succeeded': 1}

        per_course_dir = tempfile.mkdtemp()
        sink = _CsvSink(os.path.join(tempfile.mkdtemp(), 'grades_summary.csv'))
        sink.write_header(['course_id'] + GRADES_SUMMARY_COLUMNS)

        with patch.object(mod.grades_module.CourseGradeReport, 'generate', staticmethod(fake_generate_missing_column)):
            status = command._run_grades('course-1', {1}, per_course_dir, sink)
        sink.close()

        assert status['status'] == 'error'
        assert not os.path.exists(os.path.join(per_course_dir, 'course-1__grades.csv'))

    def test_grade_report_err_rows_are_captured_and_failure_count_recorded(self):
        """
        Item 4 regression: CourseGradeReport._upload (tasks_helper/grades.py)
        uploads a SEPARATE grade_report_err CSV listing every learner
        CourseGradeFactory failed to grade. _invoke_report previously
        filtered this out entirely (any csv_name ending in '_err'), and
        _run_grades discarded the raw_result dict carrying
        context.task_progress.failed with `result, _ = ...`. Neither the
        rows nor the count survived. This fake is the first in this file to
        emit both a grade_report and a grade_report_err upload for the same
        call (no existing fake did this before item 4).
        """
        command = _make_command()

        def fake_generate_with_errors(_a, _b, course_id, _task_input, _action_name):
            header = ['Student ID', 'Email', 'Username'] + GRADES_SUMMARY_COLUMNS[3:]
            rows = [[1, 'a@x.com', 'alice'] + ['0.9', 'honor', 'N/A', 'N', 'N', '', 'enrolled']]
            mod.grades_module.upload_csv_to_report_store([header] + rows, 'grade_report', course_id, None)
            error_header = ['Student ID', 'Username', 'Error']
            error_rows = [[1, 'alice', 'grading exception'], [99, 'outsider', 'grading exception']]
            mod.grades_module.upload_csv_to_report_store(
                [error_header] + error_rows, 'grade_report_err', course_id, None,
            )
            return {'succeeded': 1, 'failed': 2, 'attempted': 3, 'total': 3}

        with patch.object(mod.grades_module.CourseGradeReport, 'generate', staticmethod(fake_generate_with_errors)):
            per_course_dir = tempfile.mkdtemp()
            # user_ids={1}: 'outsider' (99) is not a tenant member.
            status = command._run_grades('course-1', {1}, per_course_dir, None)

        assert status['status'] == 'success'
        # Course-wide count from CourseGradeReport's own task_progress, NOT just the
        # tenant-filtered rows actually written below.
        assert status['failed'] == 2
        assert status['failed_rows_exported'] == 1

        with open(os.path.join(per_course_dir, 'course-1__grades_errors.csv')) as f:
            error_rows_written = list(csv.reader(f))
        # Tenant-filtered: user 99 ('outsider') must not appear.
        assert error_rows_written == [
            ['Student ID', 'Username', 'Error'],
            ['1', 'alice', 'grading exception'],
        ]

    def test_no_grade_report_err_upload_means_no_failed_keys_in_status(self):
        """
        When CourseGradeReport._upload didn't capture a grade_report_err
        upload at all (the common case: no failures), _run_grades must not
        fabricate 'failed'/'failed_rows_exported' keys or write an errors file.
        """
        command = _make_command()

        def fake_generate_no_errors(_a, _b, course_id, _task_input, _action_name):
            header = ['Student ID', 'Email', 'Username'] + GRADES_SUMMARY_COLUMNS[3:]
            rows = [[1, 'a@x.com', 'alice'] + ['0.9', 'honor', 'N/A', 'N', 'N', '', 'enrolled']]
            mod.grades_module.upload_csv_to_report_store([header] + rows, 'grade_report', course_id, None)
            return {'succeeded': 1, 'failed': 0}

        with patch.object(mod.grades_module.CourseGradeReport, 'generate', staticmethod(fake_generate_no_errors)):
            per_course_dir = tempfile.mkdtemp()
            status = command._run_grades('course-1', {1}, per_course_dir, None)

        assert status['status'] == 'success'
        assert 'failed' not in status
        assert 'failed_rows_exported' not in status
        assert not os.path.exists(os.path.join(per_course_dir, 'course-1__grades_errors.csv'))

    def test_grades_error_row_failure_does_not_flip_a_successful_course_to_error(self):
        """
        Grades-error-isolation regression: the error-row-handling block
        (writing <course_id>__grades_errors.csv, filtering by 'Student ID')
        runs AFTER the primary grades CSV/summary rows are already written to
        disk. If anything in that block raises, it must not retroactively
        flip an otherwise-successful course to 'error' and misreport grades
        data that was actually written correctly. Triggered here with an
        error_header missing 'Student ID' -- _filter_rows_by_column raises
        CommandError, which must be caught by the error-row block's OWN
        try/except, not the outer one.
        """
        command = _make_command()

        def fake_generate_broken_error_header(_a, _b, course_id, _task_input, _action_name):
            header = ['Student ID', 'Email', 'Username'] + GRADES_SUMMARY_COLUMNS[3:]
            rows = [[1, 'a@x.com', 'alice'] + ['0.9', 'honor', 'N/A', 'N', 'N', '', 'enrolled']]
            mod.grades_module.upload_csv_to_report_store([header] + rows, 'grade_report', course_id, None)
            # Deliberately missing 'Student ID' -- _filter_rows_by_column raises.
            error_header = ['Username', 'Error']
            error_rows = [['alice', 'grading exception']]
            mod.grades_module.upload_csv_to_report_store(
                [error_header] + error_rows, 'grade_report_err', course_id, None,
            )
            return {'succeeded': 1, 'failed': 1}

        with patch.object(
            mod.grades_module.CourseGradeReport, 'generate', staticmethod(fake_generate_broken_error_header)
        ):
            per_course_dir = tempfile.mkdtemp()
            status = command._run_grades('course-1', {1}, per_course_dir, None)

        # The course itself is still a success -- the primary grades file was fine.
        assert status['status'] == 'success'
        assert 'grades_errors_export_error' in status
        # 'failed' is read from raw_result/error_rows BEFORE the try block that goes
        # on to fail -- it's safe to record (no IO) and must survive this failure,
        # unlike 'failed_rows_exported' which depends on the filtering that raised.
        assert status['failed'] == 1
        assert 'failed_rows_exported' not in status
        assert os.path.exists(os.path.join(per_course_dir, 'course-1__grades.csv'))
        # The errors file itself was never written -- the failure happened before that write.
        assert not os.path.exists(os.path.join(per_course_dir, 'course-1__grades_errors.csv'))


class RunStudentFeaturesTests(TestCase):
    """
    Tests for Command._run_student_features -- the profiles/enrollments
    split and the tenant-wide learner_profile.csv dedup (see module docstring).
    """

    def _fake_upload_students_csv(self, _a, _b, course_id, feature_list, _action_name):
        header = list(feature_list)
        idx = {c: i for i, c in enumerate(header)}

        def row_for(user_id, username, enrollment_mode):
            row = [''] * len(header)
            if 'id' in idx:
                # Mirrors the REAL enrolled_students_features behavior, not a
                # convenient shortcut: extract_attr (instructor_analytics/basic.py)
                # calls DjangoJSONEncoder().default(attr) directly, which
                # unconditionally raises TypeError for a plain int (verified:
                # json.JSONEncoder().default(42) raises), so its except branch
                # stringifies -- 'id' comes back as '1', not 1. A fake that used
                # a raw int here would hide the exact bug this test guards against.
                row[idx['id']] = str(user_id)
            if 'username' in idx:
                row[idx['username']] = username
            if 'enrollment_mode' in idx:
                row[idx['enrollment_mode']] = enrollment_mode
            return row

        rows = [row_for(1, 'alice', 'honor'), row_for(2, 'bob', 'verified'), row_for(99, 'outsider', 'honor')]
        mod.enrollments_module.upload_csv_to_report_store([header] + rows, 'student_profile_info', course_id, None)
        return {'succeeded': 3}

    def test_dedups_learner_profile_across_courses_and_filters_non_members(self):
        command = _make_command()
        feature_list = command._build_feature_list(['id', 'username'], ['profiles', 'enrollments'])
        output_dir = tempfile.mkdtemp()
        per_course_dir = tempfile.mkdtemp()
        sinks = command._open_sinks(output_dir, ['profiles', 'enrollments'], feature_list)
        seen = set()

        with patch.object(mod.enrollments_module, 'upload_students_csv', self._fake_upload_students_csv):
            status1 = command._run_student_features(
                'course-1', {1, 2}, feature_list, per_course_dir, True, True,
                sinks['learner_profile'], sinks['course_enrollments'], seen,
            )
            status2 = command._run_student_features(
                'course-2', {1, 2}, feature_list, per_course_dir, True, True,
                sinks['learner_profile'], sinks['course_enrollments'], seen,
            )
        for sink in sinks.values():
            sink.close()

        # 99 ('outsider') is filtered out of both courses.
        assert status1 == {'status': 'success', 'rows': 2}
        assert status2 == {'status': 'success', 'rows': 2}

        with open(os.path.join(output_dir, 'learner_profile.csv')) as f:
            profile_rows = list(csv.DictReader(f))
        # Deduped on learner id across course-1 and course-2: 2 learners, not 4.
        assert len(profile_rows) == 2
        assert 'enrollment_mode' not in profile_rows[0]

        with open(os.path.join(output_dir, 'course_enrollments.csv')) as f:
            enrollment_rows = list(csv.DictReader(f))
        # One row per (course, learner): 2 courses x 2 members = 4.
        assert len(enrollment_rows) == 4
        assert {'course_id', 'id', 'enrollment_mode'}.issubset(enrollment_rows[0].keys())

    def test_all_rows_filtered_out_is_reported_as_a_warning_not_silent_success(self):
        """
        Regression test: an operator must see this in the manifest, not
        discover it later from an empty off-boarding bundle. This is the
        exact shape the string/int 'id' mismatch bug produced before
        _filter_rows_by_column was fixed to compare as strings.
        """
        command = _make_command()

        def fake_all_outsiders(_a, _b, course_id, feature_list, _action_name):
            header = list(feature_list)
            idx = {c: i for i, c in enumerate(header)}
            row = [''] * len(header)
            row[idx['id']] = '999'
            mod.enrollments_module.upload_csv_to_report_store([header] + [row], 'student_profile_info', course_id, None)
            return {'succeeded': 1}

        feature_list = command._build_feature_list(['id'], ['profiles'])
        sinks = command._open_sinks(tempfile.mkdtemp(), ['profiles'], feature_list)
        with patch.object(mod.enrollments_module, 'upload_students_csv', fake_all_outsiders):
            status = command._run_student_features(
                'course-1', {1, 2}, feature_list, tempfile.mkdtemp(), True, False,
                sinks['learner_profile'], None, set(),
            )
        for sink in sinks.values():
            sink.close()

        assert status['status'] == 'success'
        assert status['rows'] == 0
        assert 'warning' in status


class RunProblemResponsesTests(TestCase):
    """
    Tests for Command._run_problem_responses -- username-based tenant
    filter and the task_input shape (problem_locations as a string, not a
    list; user_id from the --as-user operator).
    """

    def test_filters_by_username_and_passes_course_root_usage_key(self):
        command = _make_command()
        captured_task_input = {}

        def fake_generate(_a, _b, course_id, task_input, _action_name):
            captured_task_input.update(task_input)
            header = ['username', 'title', 'location', 'block_key', 'state']
            rows = [
                ['alice', 'P1', 'loc', 'blk-1', 'state1'],
                ['outsider', 'P1', 'loc', 'blk-1', 'state2'],
            ]
            mod.grades_module.upload_csv_to_report_store([header] + rows, 'student_state', course_id, None)
            return {'succeeded': 2}

        operator = MagicMock(id=42)
        with patch.object(mod.grades_module.ProblemResponses, 'generate', staticmethod(fake_generate)), \
                patch.object(mod, 'modulestore') as mock_modulestore:
            mock_modulestore.return_value.make_course_usage_key.return_value = 'block-v1:course+root'
            per_course_dir = tempfile.mkdtemp()
            status = command._run_problem_responses('course-1', {'alice', 'bob'}, operator, per_course_dir, None)

        assert status == {'status': 'success', 'rows': 1}
        assert captured_task_input['user_id'] == 42
        assert isinstance(captured_task_input['problem_locations'], str)


class RunOra2Tests(TestCase):
    """
    Tests for Command._run_ora2 -- the 'failed' sentinel must be treated as
    an error, and an unverifiable identity column must skip rather than
    write an unfiltered cross-tenant export.
    """

    def test_failed_sentinel_is_treated_as_an_error(self):
        command = _make_command()
        with patch.object(mod.misc_module, 'upload_ora2_data', lambda *a, **k: 'failed'):
            status = command._run_ora2('course-1', {1}, None, tempfile.mkdtemp(), None)
        assert status['status'] == 'error'

    def test_unverifiable_identity_column_is_skipped_not_written_unfiltered(self):
        command = _make_command()
        course = CourseOverviewFactory()

        def fake_generate(_a, _b, course_id, _task_input, _action_name):
            header = ['Submission ID', 'Response Text']
            rows = [['sub-1', 'free text']]
            mod.misc_module.upload_csv_to_report_store([header] + rows, 'ORA_data', course_id, None)
            return {'succeeded': 1}

        with patch.object(mod.misc_module, 'upload_ora2_data', fake_generate):
            status = command._run_ora2(course.id, {1}, None, tempfile.mkdtemp(), None)

        assert status == {'status': 'skipped', 'reason': 'identity column unverified'}

    def test_detected_identity_column_filters_to_tenant(self):
        command = _make_command()
        course = CourseOverviewFactory()
        member = UserFactory()
        other_tenant_user = UserFactory()
        # Detection needs the FULL course-scoped superset (both rows) to
        # recognize the column at all; the tenant SET used for the actual
        # filter only contains `member`'s anon id -- this is exactly the
        # "course has more than one tenant's students" scenario the
        # content-based detection exists to handle safely (see module
        # docstring's ora2 gap).
        AnonymousUserId.objects.create(user=member, course_id=course.id, anonymous_user_id='a' * 32)
        AnonymousUserId.objects.create(user=other_tenant_user, course_id=course.id, anonymous_user_id='b' * 32)

        def fake_generate(_a, _b, course_id, _task_input, _action_name):
            header = ['Submission ID', 'Anon Col', 'Response Text']
            rows = [
                ['sub-1', 'a' * 32, 'in tenant'],
                ['sub-2', 'b' * 32, 'not in tenant'],
            ]
            mod.misc_module.upload_csv_to_report_store([header] + rows, 'ORA_data', course_id, None)
            return {'succeeded': 2}

        with patch.object(mod.misc_module, 'upload_ora2_data', fake_generate):
            status = command._run_ora2(course.id, {member.id}, None, tempfile.mkdtemp(), None)

        assert status['status'] == 'success'
        assert status['rows'] == 1
        assert status['identity_column'] == 'Anon Col'

    def test_override_column_filters_against_the_union_of_identity_spaces(self):
        """
        Regression test: --ora2-identity-column must not assume the
        operator-confirmed column is a raw user-id column. It could just as
        well be an anonymized-id or username column (that's the whole point
        of the flag: detection couldn't verify it by content). Matching
        against user_ids alone silently zeroed out every override run whose
        column actually held anon ids or usernames.
        """
        command = _make_command()
        course = CourseOverviewFactory()
        member = UserFactory()
        AnonymousUserId.objects.create(user=member, course_id=course.id, anonymous_user_id='a' * 32)

        def fake_generate(_a, _b, course_id, _task_input, _action_name):
            header = ['Submission ID', 'Confirmed Anon Col', 'Response Text']
            rows = [
                ['sub-1', 'a' * 32, 'in tenant'],
                ['sub-2', 'b' * 32, 'not in tenant'],
            ]
            mod.misc_module.upload_csv_to_report_store([header] + rows, 'ORA_data', course_id, None)
            return {'succeeded': 2}

        with patch.object(mod.misc_module, 'upload_ora2_data', fake_generate):
            status = command._run_ora2(course.id, {member.id}, 'Confirmed Anon Col', tempfile.mkdtemp(), None)

        assert status['status'] == 'success'
        assert status['rows'] == 1


class RunMayEnrollTests(TestCase):
    """
    Tests for Command._run_may_enroll -- deliberately NOT tenant-filtered
    (see module docstring); the caveat must be present in the returned status.
    """

    def test_returns_unfiltered_rows_with_a_caveat_flag(self):
        command = _make_command()

        def fake_may_enroll(_a, _b, course_id, task_input, _action_name):
            header = list(task_input['features'])
            rows = [['someone@example.com', True, '2026-01-01']]
            mod.enrollments_module.upload_csv_to_report_store([header] + rows, 'may_enroll_info', course_id, None)
            return {'succeeded': 1}

        with patch.object(mod.enrollments_module, 'upload_may_enroll_csv', fake_may_enroll):
            status = command._run_may_enroll('course-1', tempfile.mkdtemp(), None)

        assert status['status'] == 'success'
        assert status['caveat'] == 'not tenant-membership-filtered'


class ProcessCourseTests(TestCase):
    """
    Tests for Command._process_course -- dispatches to every requested
    report type and merges their statuses under the right --reports keys,
    with 'profiles' and 'enrollments' sharing a single student_features call.
    """

    def test_dispatches_every_requested_report_and_shares_student_features_call(self):
        command = _make_command()
        call_count = {'student_features': 0}

        def fake_upload_students_csv(_a, _b, course_id, feature_list, _action_name):
            call_count['student_features'] += 1
            header = list(feature_list)
            mod.enrollments_module.upload_csv_to_report_store([header] + [], 'student_profile_info', course_id, None)
            return {'succeeded': 0}

        with patch.object(mod.enrollments_module, 'upload_students_csv', fake_upload_students_csv):
            feature_list = command._build_feature_list(['id'], ['profiles', 'enrollments'])
            sinks = command._open_sinks(tempfile.mkdtemp(), ['profiles', 'enrollments'], feature_list)
            status = command._process_course(
                'course-1', ['profiles', 'enrollments'], {1}, set(), feature_list, None, None,
                tempfile.mkdtemp(), sinks, set(),
            )
            for sink in sinks.values():
                sink.close()

        assert set(status.keys()) == {'profiles', 'enrollments'}
        # One upload_students_csv call serves both report keys.
        assert call_count['student_features'] == 1


class WriteManifestTests(TestCase):
    """
    Tests for Command._write_manifest.
    """

    def test_writes_valid_json_with_0600_permissions(self):
        command = _make_command()
        output_dir = tempfile.mkdtemp()
        manifest = {'slug': 'acme', 'courses': {}, 'summary_files': {}}

        command._write_manifest(output_dir, manifest)

        path = os.path.join(output_dir, 'MANIFEST.json')
        with open(path) as f:
            loaded = json.load(f)
        assert loaded == manifest
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


class SafeCourseIdTests(TestCase):
    """
    Tests for the module-level _safe_course_id helper.
    """

    def test_sanitizes_colons_slashes_and_plus_signs(self):
        assert mod._safe_course_id('course-v1:org+course+run') == 'course-v1_org_course_run'


class MakePrivateDirTests(TestCase):
    """
    Tests for Command._make_private_dir (item 2) -- --output-dir can point
    at a path this command didn't create (e.g. MEDIA_ROOT itself); only a
    directory this call actually creates should get chmod'd to 0700.
    """

    def test_freshly_created_directory_gets_0700(self):
        command = _make_command()
        base = tempfile.mkdtemp()
        path = os.path.join(base, 'new_export_dir')
        command._make_private_dir(path)
        assert os.path.exists(path)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o700

    def test_preexisting_directory_permissions_are_left_untouched(self):
        path = tempfile.mkdtemp()
        os.chmod(path, 0o755)
        command = _make_command()
        command._make_private_dir(path)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o755


class MaxProblemResponsesOverrideTests(TestCase):
    """
    Tests for Command._resolve_max_problem_responses and
    _override_max_problem_responses_limit (item 5) -- lifting the
    MAX_PROBLEM_RESPONSES_COUNT cap is opt-in via --max-problem-responses,
    not automatic just because problem_responses was requested.
    """

    def test_resolve_returns_none_when_flag_not_passed(self):
        command = _make_command()
        assert command._resolve_max_problem_responses(None) is None

    def test_resolve_parses_unlimited_literal(self):
        command = _make_command()
        assert command._resolve_max_problem_responses('unlimited') == 'unlimited'

    def test_resolve_parses_integer_value(self):
        command = _make_command()
        assert command._resolve_max_problem_responses('10000') == 10000

    def test_resolve_rejects_non_integer_non_unlimited_value(self):
        command = _make_command()
        with self.assertRaises(CommandError):
            command._resolve_max_problem_responses('not-a-number')

    def test_cap_left_untouched_when_override_not_requested(self):
        # Mirrors handle()'s own branch: max_problem_responses is None ->
        # _null_context, FEATURES is never touched.
        original = settings.FEATURES.get('MAX_PROBLEM_RESPONSES_COUNT')
        with mod._null_context():
            assert settings.FEATURES.get('MAX_PROBLEM_RESPONSES_COUNT') == original

    def test_cap_overridden_to_none_and_restored_after(self):
        settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] = 5000
        with mod._override_max_problem_responses_limit(None):
            assert settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] is None
        assert settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] == 5000

    def test_cap_overridden_to_explicit_integer_and_restored_after(self):
        settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] = 5000
        with mod._override_max_problem_responses_limit(20000):
            assert settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] == 20000
        assert settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] == 5000

    def test_cap_restored_even_if_the_run_raises(self):
        settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] = 5000
        with self.assertRaises(ValueError):
            with mod._override_max_problem_responses_limit(None):
                raise ValueError("simulated failure mid-run")
        assert settings.FEATURES['MAX_PROBLEM_RESPONSES_COUNT'] == 5000


class ReadReplicaRoutingTests(TestCase):
    """
    Tests for item 11 -- this command's own direct queries (tenant/org/
    course/user resolution, ORA2 identity-column detection) route through
    read_replica_or_default() (common.djangoapps.util.query, the same
    pattern cache_programs.py uses). These assert the command actually
    CALLS read_replica_or_default() at each query site -- deterministic,
    and deliberately does NOT assert a specific DB-connection exception
    class for a bad alias (that varies across Django versions, and this
    environment has no working Django install to verify it against --
    see this file's own module docstring / item 13).
    """

    def _recording_wrapper(self, calls):
        real = mod.read_replica_or_default

        def recording(*args, **kwargs):
            calls.append(True)
            return real(*args, **kwargs)
        return recording

    def test_get_sub_org_uses_read_replica(self):
        sub_org = EdlySubOrganizationFactory()
        command = _make_command()
        calls = []
        with patch.object(mod, 'read_replica_or_default', self._recording_wrapper(calls)):
            command._get_sub_org(sub_org.slug)
        assert calls

    def test_get_user_ids_uses_read_replica(self):
        sub_org = EdlySubOrganizationFactory()
        command = _make_command()
        calls = []
        with patch.object(mod, 'read_replica_or_default', self._recording_wrapper(calls)):
            command._get_user_ids(sub_org)
        assert calls

    def test_get_tenant_course_ids_uses_read_replica(self):
        command = _make_command()
        calls = []
        with patch.object(mod, 'read_replica_or_default', self._recording_wrapper(calls)):
            command._get_tenant_course_ids([], set())
        assert calls

    def test_resolve_operator_uses_read_replica(self):
        user = UserFactory()
        command = _make_command()
        calls = []
        with patch.object(mod, 'read_replica_or_default', self._recording_wrapper(calls)):
            command._resolve_operator(user.username)
        assert calls

    def test_detect_ora2_identity_column_uses_read_replica(self):
        course = CourseOverviewFactory()
        command = _make_command()
        calls = []
        with patch.object(mod, 'read_replica_or_default', self._recording_wrapper(calls)):
            command._detect_ora2_identity_column(['a', 'b'], [['1', '2']], course.id, None)
        assert calls

    def test_bogus_replica_alias_breaks_the_query(self):
        """
        Secondary check, not the primary proof (see class docstring):
        asserts broad Exception, not a named Django exception class.
        """
        sub_org = EdlySubOrganizationFactory()
        command = _make_command()
        with patch.object(mod, 'read_replica_or_default', lambda: 'definitely-not-a-real-alias'):
            with self.assertRaises(Exception):
                command._get_sub_org(sub_org.slug)


class HandleTests(TestCase):
    """
    Tests for Command.handle() itself (item 12) -- built from the real
    argument parser, not hand-written option dicts. Covers the parser's
    own defaults, --dry-run, the no-members early return, the --as-user/
    is_staff gating for problem_responses (items 7 + 12), the may_enroll
    opt-in warning, and (with Command._process_course patched to raise
    partway through a multi-course run) the interrupted-run try/finally
    resilience added for item 3.
    """

    def _parse(self, *cli_args):
        command = Command()
        parser = command.create_parser('manage.py', 'export_tenant_reports_csv')
        return vars(parser.parse_args(list(cli_args)))

    def test_parser_defaults(self):
        options = self._parse('some-slug')
        assert options['reports'] == ','.join(DEFAULT_REPORTS)
        assert options['as_user'] is None
        assert options['dry_run'] is False
        assert options['output_dir'] is None
        assert options['include_fields'] is None
        assert options['allow_meta_field'] is False
        assert options['skip_course'] == []
        assert options['ora2_identity_column'] is None
        assert options['max_problem_responses'] is None

    def test_dry_run_writes_nothing(self):
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        out = StringIO()
        output_dir = os.path.join(tempfile.mkdtemp(), 'would-be-output')
        # --reports grades (NOT the default reports list): the --as-user/is_staff
        # requirement for problem_responses (item 7) is checked BEFORE the dry_run
        # branch, and DEFAULT_REPORTS includes problem_responses -- without this,
        # --dry-run with no --as-user would raise CommandError before ever reaching
        # the dry-run branch (caught by this suite's own stub-harness proof run).
        call_command(
            'export_tenant_reports_csv', sub_org.slug, '--dry-run', '--reports', 'grades',
            '--output-dir', output_dir, stdout=out,
        )
        assert 'DRY RUN' in out.getvalue()
        assert not os.path.exists(output_dir)

    def test_no_members_early_return(self):
        sub_org = EdlySubOrganizationFactory()
        out = StringIO()
        call_command('export_tenant_reports_csv', sub_org.slug, stdout=out)
        assert 'nothing to export' in out.getvalue()

    def test_problem_responses_without_as_user_raises(self):
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        with self.assertRaises(CommandError):
            call_command('export_tenant_reports_csv', sub_org.slug, '--reports', 'problem_responses')

    def test_problem_responses_as_user_non_staff_raises(self):
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        operator = UserFactory(is_staff=False)
        with self.assertRaises(CommandError):
            call_command(
                'export_tenant_reports_csv', sub_org.slug, '--reports', 'problem_responses',
                '--as-user', operator.username,
            )

    def test_problem_responses_as_user_staff_passes_through(self):
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        operator = UserFactory(is_staff=True)
        out = StringIO()
        with patch.object(
            Command, '_process_course',
            return_value={'problem_responses': {'status': 'success', 'rows': 0}},
        ):
            call_command(
                'export_tenant_reports_csv', sub_org.slug, '--reports', 'problem_responses',
                '--as-user', operator.username, '--output-dir', tempfile.mkdtemp(), stdout=out,
            )
        assert 'Exported tenant CSV reports' in out.getvalue()

    def test_may_enroll_opt_in_warning(self):
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        out = StringIO()
        with patch.object(Command, '_process_course', return_value={}):
            call_command(
                'export_tenant_reports_csv', sub_org.slug, '--reports', 'may_enroll',
                '--output-dir', tempfile.mkdtemp(), stdout=out,
            )
        assert 'cannot be tenant-membership-filtered' in out.getvalue()

    def test_interrupted_run_leaves_incomplete_manifest_and_partial_sink_files(self):
        """
        Item 3 regression: simulate an unexpected exception partway through a
        multi-course run (Command._process_course patched to succeed once
        then raise) and assert the try/finally in handle() still closes
        every sink -- including the _DictCsvSink-backed problem_responses/
        ora2 files, which previously were never written at all on an
        interrupted run -- and still writes a manifest, marked incomplete.

        Note: this patches Command._process_course, which sits ABOVE each
        _run_*'s own per-report `except Exception` -- it proves handle()'s
        own try/finally survives an interruption, but NOT that a
        KeyboardInterrupt/SystemExit raised INSIDE a report call itself isn't
        swallowed by that inner `except Exception` (KeyboardInterrupt is a
        BaseException, not an Exception, and RuntimeError is not evidence
        either way). See
        test_keyboard_interrupt_inside_a_report_call_is_not_swallowed_by_inner_except_exception
        below for that property specifically.
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course_1 = CourseOverviewFactory()
        course_2 = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course_1.id, is_active=True)
        CourseEnrollmentFactory(user=member, course_id=course_2.id, is_active=True)
        operator = UserFactory(is_staff=True)

        output_dir = tempfile.mkdtemp()
        call_count = {'n': 0}

        def fake_process_course(self, course_id, *args, **kwargs):
            call_count['n'] += 1
            if call_count['n'] == 1:
                return {'grades': {'status': 'success', 'rows': 0}}
            raise RuntimeError('simulated unexpected failure partway through the run')

        with patch.object(Command, '_process_course', fake_process_course):
            with self.assertRaises(RuntimeError):
                call_command(
                    'export_tenant_reports_csv', sub_org.slug,
                    '--reports', 'grades,problem_responses,ora2',
                    '--as-user', operator.username,
                    '--output-dir', output_dir,
                )

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert manifest['status'] == 'incomplete'
        assert len(manifest['courses_completed']) == 1

        assert os.path.exists(os.path.join(output_dir, 'grades_summary.csv'))
        assert os.path.exists(os.path.join(output_dir, 'problem_responses.csv'))
        # 'ora2' IS in --reports above, so its _DictCsvSink must survive the
        # interruption too, same as problem_responses.csv just above.
        assert os.path.exists(os.path.join(output_dir, 'ora2_responses.csv'))

    def test_keyboard_interrupt_inside_a_report_call_is_not_swallowed_by_inner_except_exception(self):
        """
        Finally-block-hardening regression: the test above patches
        Command._process_course, which sits ABOVE _run_grades's own
        per-report `except Exception` -- it proves handle()'s own try/finally
        survives an interruption, but not that a KeyboardInterrupt raised
        INSIDE a report call itself isn't swallowed by that inner `except
        Exception`. KeyboardInterrupt/SystemExit are BaseExceptions, not
        Exceptions, so `except Exception` in _run_grades (and every other
        _run_* method) must not catch them. This patches the real
        CourseGradeReport.generate call itself to raise KeyboardInterrupt, so
        it must propagate all the way out of call_command(), while still
        leaving a written, 'incomplete' manifest. (An external OOM-kill is
        SIGKILL -- no Python code runs at all, so nothing in this command can
        survive that; only an in-process KeyboardInterrupt or MemoryError are
        actually within reach of this try/finally -- see module docstring.)
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()

        def raising_generate(*args, **kwargs):
            raise KeyboardInterrupt('simulated Ctrl-C during CourseGradeReport.generate')

        with patch.object(mod.grades_module.CourseGradeReport, 'generate', staticmethod(raising_generate)):
            with self.assertRaises(KeyboardInterrupt):
                call_command(
                    'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                    '--output-dir', output_dir,
                )

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert manifest['status'] == 'incomplete'

    def test_finally_block_survives_a_sink_close_failure_without_losing_the_manifest(self):
        """
        Finally-block-hardening regression: the finally block that's supposed
        to guarantee every sink gets flushed and the manifest gets written on
        interruption previously did that work with a single unguarded loop --
        _DictCsvSink.close() opens a file and encodes/writes every buffered
        row, so if THAT raised (disk full, encode error, MemoryError...), the
        loop aborted before the remaining sinks closed or the manifest was
        written at all -- exactly the failure this finally block exists to
        prevent. This patches Command._open_sinks to return one broken sink
        alongside a real one, and Command._process_course to raise partway
        through (simulating the ORIGINAL interruption this finally block
        exists for), asserting: the original exception still propagates (not
        replaced/masked by the broken sink's own close() failure), the good
        sink's file still lands on disk, and MANIFEST.json still gets
        written -- recording the broken sink's failure instead of losing
        everything.

        The broken sink is placed FIRST in the dict Command._open_sinks
        returns (an OrderedDict -- insertion order is only guaranteed dict
        behavior from Python 3.7 on, and this file still targets py35 per
        tox.ini, so plain dict literal order isn't a safe thing to depend on
        here). Order is load-bearing: this is the only arrangement that
        actually proves a failing sink doesn't stop the sinks AFTER it from
        closing -- good-first would let the good sink close before the
        broken one ever raises, proving nothing. The good sink is a
        _DictCsvSink (not _CsvSink): _CsvSink.__init__ creates its file via
        os.open(..., O_CREAT) immediately, so os.path.exists() on one would
        already be true before call_command() even runs, making that
        assertion pass regardless of whether close() ran. _DictCsvSink only
        creates its file inside close(), so os.path.exists() here is a real,
        load-bearing proof that close() executed on this sink specifically.

        --reports is still just 'grades' -- Command._open_sinks is patched
        out entirely, so the sink names/types below don't need to match the
        requested reports. ('problem_responses' as a --reports value would
        additionally require --as-user/is_staff, an unrelated gate this test
        isn't exercising.)
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        good_sink_path = os.path.join(output_dir, 'problem_responses.csv')
        good_sink = _DictCsvSink(good_sink_path)
        good_sink.add_rows('course-1', ['username'], [['alice']])

        class _BrokenSink(object):
            # Nonzero and known before close() runs -- 0 would be indistinguishable
            # from a placeholder default and wouldn't prove this count is the sink's
            # real, pre-close value.
            row_count = 3

            def close(self):
                raise MemoryError('simulated: this sink failed to flush/close')

        def raising_process_course(self, course_id, *args, **kwargs):
            raise RuntimeError('simulated interruption (e.g. Ctrl-C) during _process_course')

        def fake_open_sinks(self, output_dir, reports, feature_list, sinks=None):
            # Broken sink FIRST: proves a failing close() doesn't cost the
            # sinks after it their close(), which is the actual regression.
            # sinks= accepted (and ignored) only so this fake's signature still
            # matches handle()'s call, which now passes its own pre-initialized
            # dict through as a 4th positional arg (see _open_sinks docstring).
            return OrderedDict([('broken', _BrokenSink()), ('problem_responses', good_sink)])

        with patch.object(Command, '_process_course', raising_process_course), \
                patch.object(Command, '_open_sinks', fake_open_sinks):
            with self.assertRaises(RuntimeError):
                call_command(
                    'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                    '--output-dir', output_dir,
                )

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert manifest['status'] == 'incomplete'
        # row_count is read before close() (inside the same guarded try as close()
        # itself), so the broken sink's count (3) is still recorded even though its
        # close() raised -- the error itself lands on the separate summary_file_errors
        # key, not summary_files.
        assert manifest['summary_files']['broken.csv'] == 3
        assert manifest['summary_file_errors']['broken.csv']
        assert manifest['summary_files']['problem_responses.csv'] == 1
        # Only exists if close() actually ran on the sink AFTER the broken one.
        assert os.path.exists(good_sink_path)

    def test_write_manifest_failure_does_not_mask_the_original_interruption_exception(self):
        """
        Finally-block-hardening regression: _write_manifest itself is now
        guarded in its own try/except (previously unguarded, even after the
        per-sink close() isolation above was added) -- if writing
        MANIFEST.json fails (disk full, IO error), that failure must be
        logged and swallowed, not allowed to replace/mask whatever original
        exception (KeyboardInterrupt, MemoryError, or here a simulated
        RuntimeError from _process_course) triggered the finally block in
        the first place -- that original exception is the operator's only
        signal about why the run actually stopped.
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()

        def raising_process_course(self, course_id, *args, **kwargs):
            raise RuntimeError('simulated interruption (e.g. Ctrl-C) during _process_course')

        def raising_write_manifest(self, output_dir, manifest):
            raise IOError('simulated: disk full while writing MANIFEST.json')

        with patch.object(Command, '_process_course', raising_process_course), \
                patch.object(Command, '_write_manifest', raising_write_manifest):
            with self.assertRaises(RuntimeError):
                call_command(
                    'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                    '--output-dir', output_dir,
                )

        # The manifest-write failure is real (not a no-op) -- MANIFEST.json never landed.
        assert not os.path.exists(os.path.join(output_dir, 'MANIFEST.json'))

    def test_write_manifest_failure_on_a_successful_run_is_not_reported_as_success(self):
        """
        The finally block's _write_manifest guard must not turn a lost audit
        trail into a clean exit. With no original exception in flight there
        is nothing to mask, so a manifest-write failure on an otherwise
        successful run must surface, not be swallowed behind the success banner.
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)
        output_dir = tempfile.mkdtemp()
        out = StringIO()
        err = StringIO()

        def raising_write_manifest(self, output_dir, manifest):
            raise IOError('simulated: disk full while writing MANIFEST.json')

        with patch.object(Command, '_process_course', return_value={'grades': {'status': 'success', 'rows': 0}}), \
                patch.object(Command, '_write_manifest', raising_write_manifest):
            with self.assertRaises(CommandError):
                call_command(
                    'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                    '--output-dir', output_dir, stdout=out, stderr=err,
                )
        assert not os.path.exists(os.path.join(output_dir, 'MANIFEST.json'))
        # The raise happens before the success banner is written -- this is the actual
        # "not reported as success" the test name promises, not just the CommandError itself.
        assert 'Exported tenant CSV reports' not in out.getvalue()
        # Previously only the raised exception TYPE was checked here, not the
        # operator-facing message -- half of what this fix is actually for: an
        # operator watching stderr, not just a caller checking the exit code.
        assert 'MANIFEST.json could not be written' in err.getvalue()

    def test_successful_run_with_a_failing_sink_close_raises_and_still_writes_manifest(self):
        """
        Generalization regression: the "an operator must not get a clean exit
        0" principle the test above established for a lost MANIFEST.json
        applies equally to a lost summary CSV. Previously a sink's close()
        failing on an otherwise-successful run was recorded ONLY in
        manifest['summary_file_errors'] -- a JSON key nobody reads on a run
        that prints the success banner and exits 0 -- so an entire data CSV
        (e.g. problem_responses.csv) could silently never land on disk.

        The manifest write must still happen BEFORE this raise (see the
        module docstring and the comment above the finally block's
        `if run_succeeded:` guard), so summary_file_errors itself survives on
        disk even though the run as a whole now fails loudly.
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        out = StringIO()
        err = StringIO()

        class _BrokenSink(object):
            row_count = 4  # nonzero and known-before-close, same discipline as summary_files elsewhere.

            def close(self):
                raise IOError('simulated: disk full while flushing problem_responses.csv')

        def fake_open_sinks(self, output_dir, reports, feature_list, sinks=None):
            return OrderedDict([('problem_responses', _BrokenSink())])

        with patch.object(Command, '_process_course', return_value={'grades': {'status': 'success', 'rows': 0}}), \
                patch.object(Command, '_open_sinks', fake_open_sinks):
            with self.assertRaises(CommandError):
                call_command(
                    'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                    '--output-dir', output_dir, stdout=out, stderr=err,
                )

        # MANIFEST.json IS still on disk -- written before the raise, unlike the
        # write-manifest-failure case above where the write itself is what failed.
        manifest_path = os.path.join(output_dir, 'MANIFEST.json')
        assert os.path.exists(manifest_path)
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest['summary_file_errors']['problem_responses.csv']
        # Round-6 addition: the count-before-close() behavior (see the finally block's
        # own comment) is actually exercised here on the success path it exists for --
        # previously this test only checked summary_file_errors, never summary_files.
        assert manifest['summary_files']['problem_responses.csv'] == 4
        # The run itself completed with no per-course report errors -- only the sink's
        # own close() failed, a separate concern from the course loop's own success --
        # so status stays plain 'complete', not 'complete_with_errors'.
        assert manifest['status'] == 'complete'

        assert 'Exported tenant CSV reports' not in out.getvalue()
        assert 'problem_responses.csv' in err.getvalue()

    def test_courses_completed_excludes_skip_course_entries(self):
        """
        Finally-block-hardening regression: manifest['courses_completed']
        previously listed every key in manifest['courses'], including courses
        skipped via --skip-course (recorded as {'_skipped': '--skip-course'})
        -- those were never actually processed, so they must not count as
        "completed".
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course_1 = CourseOverviewFactory()
        course_2 = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course_1.id, is_active=True)
        CourseEnrollmentFactory(user=member, course_id=course_2.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        with patch.object(Command, '_process_course', return_value={'grades': {'status': 'success', 'rows': 0}}):
            call_command(
                'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                '--skip-course', str(course_2.id),
                '--output-dir', output_dir,
            )

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert manifest['courses'][str(course_2.id)] == {'_skipped': '--skip-course'}
        assert manifest['courses_completed'] == [str(course_1.id)]
        # Round-6 addition: a --skip-course entry (a flat {'_skipped': ...} dict, not a
        # per-report status dict) must not be misread as a course-level error by
        # _course_has_report_error, and the one real course here was a plain success --
        # so this run's status must stay 'complete', not be downgraded.
        assert manifest['courses_with_errors'] == []
        assert manifest['status'] == 'complete'

    def test_known_gaps_includes_enrollment_mismatch_even_without_ora2(self):
        """
        known_gaps-ungating regression: the active/inactive enrollment-
        membership mismatch between learner_profile.csv and
        grades_summary.csv (see module docstring) affects any --reports
        grades,profiles run, not just when ora2 is requested -- previously
        manifest['known_gaps'] was only ever populated when 'ora2' was in the
        requested reports, so an operator running a plain grades+profiles
        export never saw this caveat surfaced in MANIFEST.json at all, only
        in the module docstring.
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        with patch.object(Command, '_process_course', return_value={}):
            call_command(
                'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades,profiles',
                '--output-dir', output_dir,
            )

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert 'enrollment_membership_mismatch' in manifest['known_gaps']
        assert 'ora2_identity_column_detection' not in manifest['known_gaps']
        # 'ora2' was never in --reports, so _open_sinks never opened an ora2 sink --
        # this file must not exist (previously a stray assertion here claimed it did,
        # copy-pasted from the interruption test above, which DOES request ora2).
        assert not os.path.exists(os.path.join(output_dir, 'ora2_responses.csv'))

    def test_all_course_report_errors_downgrade_status_and_banner_but_do_not_raise(self):
        """
        Round-6 fix: previously, a run in which EVERY course's EVERY report
        returned {'status': 'error', ...} (no exception raised, no
        interruption -- per-course isolation working exactly as designed)
        still exited 0, still printed the plain unconditional success
        banner, and still recorded manifest['status'] == 'complete', giving
        an operator no top-level signal that every single course actually
        failed. Per-course isolation itself is correct and must NOT change
        here -- a broken course must not abort the whole run, so this must
        NOT raise -- but the run's own summary must reflect what happened:
        manifest['status'] downgrades to 'complete_with_errors',
        manifest['courses_with_errors'] names the affected courses, and the
        final stdout banner becomes a WARNING naming the error count instead
        of the plain success line.
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course_1 = CourseOverviewFactory()
        course_2 = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course_1.id, is_active=True)
        CourseEnrollmentFactory(user=member, course_id=course_2.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        out = StringIO()
        err = StringIO()

        def all_errors_process_course(self, course_id, *args, **kwargs):
            return {'grades': {'status': 'error', 'error': 'simulated generator explosion'}}

        with patch.object(Command, '_process_course', all_errors_process_course):
            # Must NOT raise -- per-course isolation means a run with only per-course
            # report errors (no exception, no interruption) still completes.
            call_command(
                'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                '--output-dir', output_dir, stdout=out, stderr=err,
            )

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert manifest['status'] == 'complete_with_errors'
        assert sorted(manifest['courses_with_errors']) == sorted([str(course_1.id), str(course_2.id)])
        # Neither course had a warning/skipped report -- only outright errors -- so the
        # separate, weaker courses_needing_review rollup must stay empty here.
        assert manifest['courses_needing_review'] == []
        # The plain, unconditional success banner text must not appear anywhere in stdout.
        assert 'Exported tenant CSV reports' not in out.getvalue()
        # Whatever warning IS printed must name the course-error count.
        assert '2 course(s)' in out.getvalue()
        assert 'MANIFEST.json' in out.getvalue()

    def test_both_sink_close_and_manifest_write_failing_still_names_the_sink_on_stderr(self):
        """
        Round-6 fix: previously, when BOTH a sink's close() AND the
        MANIFEST.json write failed on an otherwise-successful run, the
        sink-failure-naming stderr message sat BELOW the manifest-failure
        priority raise and was therefore unreachable on this path -- so
        MANIFEST.json never landed (the write itself failed) AND stderr
        never named which CSV was lost either, leaving the operator with NO
        record of it anywhere. The sink-failure stderr message is now
        written unconditionally (whenever summary_file_errors is non-empty)
        BEFORE the raise-priority decision, so stderr becomes the operator's
        only remaining channel for that information -- even though the
        manifest-write failure still takes priority for which exception
        actually propagates (only one can).
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        out = StringIO()
        err = StringIO()

        class _BrokenSink(object):
            row_count = 4

            def close(self):
                raise IOError('simulated: disk full while flushing problem_responses.csv')

        def fake_open_sinks(self, output_dir, reports, feature_list, sinks=None):
            return OrderedDict([('problem_responses', _BrokenSink())])

        def raising_write_manifest(self, output_dir, manifest):
            raise IOError('simulated: disk full while writing MANIFEST.json')

        with patch.object(Command, '_process_course', return_value={'grades': {'status': 'success', 'rows': 0}}), \
                patch.object(Command, '_open_sinks', fake_open_sinks), \
                patch.object(Command, '_write_manifest', raising_write_manifest):
            with self.assertRaises(CommandError):
                call_command(
                    'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                    '--output-dir', output_dir, stdout=out, stderr=err,
                )

        # The manifest write itself failed -- it never landed on disk, so the sink
        # failure was never recorded there either.
        assert not os.path.exists(os.path.join(output_dir, 'MANIFEST.json'))
        # Manifest-write failure takes priority for which exception propagates --
        # but the lost sink's CSV name is still named on stderr regardless, since
        # stderr is now the operator's only remaining channel for it.
        assert 'problem_responses.csv' in err.getvalue()
        assert 'MANIFEST.json could not be written' in err.getvalue()
        assert 'Exported tenant CSV reports' not in out.getvalue()

    def test_open_sinks_partial_failure_still_closes_and_records_the_already_opened_sinks(self):
        """
        Round-6 fix: _open_sinks() raising partway through (e.g. a third
        sink's file creation fails after two already succeeded) already
        surfaced loudly -- that OSError propagates all the way out of
        handle(), an already-loud failure. But the sinks it had already
        created before that point were previously orphaned: local to
        _open_sinks' own now-abandoned local dict, never visible to
        handle()'s own `sinks` variable, so the finally block never closed
        them (a descriptor leak) and MANIFEST.json['summary_files'] never
        recorded them either. _open_sinks now mutates the SAME dict
        handle() already holds, so those earlier sinks stay reachable.
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        real_csv_sink = mod._CsvSink
        opened = []

        class _FailOnThirdSink(real_csv_sink):
            _count = [0]

            def __init__(self, path):
                _FailOnThirdSink._count[0] += 1
                if _FailOnThirdSink._count[0] == 3:
                    raise OSError('simulated: third sink failed to open (ENOSPC)')
                super(_FailOnThirdSink, self).__init__(path)
                opened.append(self)

        with patch.object(mod, '_CsvSink', _FailOnThirdSink):
            with self.assertRaises(OSError):
                # grades -> grades_summary (1st _CsvSink), profiles -> learner_profile
                # (2nd), enrollments -> course_enrollments (3rd, raises). _process_course
                # is never reached: _open_sinks() is the first statement inside handle()'s
                # try, so the course loop below it never starts.
                call_command(
                    'export_tenant_reports_csv', sub_org.slug,
                    '--reports', 'grades,profiles,enrollments',
                    '--output-dir', output_dir,
                )

        assert len(opened) == 2
        for sink in opened:
            assert sink._file.closed

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert manifest['status'] == 'incomplete'
        assert sorted(manifest['summary_files']) == ['grades_summary.csv', 'learner_profile.csv']

    def test_skipped_ora2_course_lands_in_courses_needing_review_without_touching_status(self):
        """
        Round-6 fix (2nd advisor-review finding): _run_ora2's {'status':
        'skipped', ...} outcome (an unverifiable identity column -- see
        module docstring's ora2 gap) is a deliberate abstention, not a
        failure, so it correctly does NOT count toward
        manifest['courses_with_errors'] / manifest['status'] downgrading.
        But that course's ora2 data really is missing from this export, and
        an operator must not have to know to go dig through every per-course
        entry in MANIFEST.json['courses'] to discover that -- it belongs in
        the separate, weaker manifest['courses_needing_review'] rollup, and
        the final banner must still be qualified, not the plain success line.
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        out = StringIO()

        def skipped_ora2_process_course(self, course_id, *args, **kwargs):
            return {'ora2': {'status': 'skipped', 'reason': 'identity column unverified'}}

        with patch.object(Command, '_process_course', skipped_ora2_process_course):
            call_command(
                'export_tenant_reports_csv', sub_org.slug, '--reports', 'ora2',
                '--output-dir', output_dir, stdout=out,
            )

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert manifest['courses_needing_review'] == [str(course.id)]
        # Not a failure -- must not be conflated with the stronger courses_with_errors signal.
        assert manifest['courses_with_errors'] == []
        assert manifest['status'] == 'complete'
        assert 'Exported tenant CSV reports' not in out.getvalue()
        assert 'warning or skipped report' in out.getvalue()

    def test_all_rows_filtered_warning_lands_in_courses_needing_review_without_touching_status(self):
        """
        Round-6 fix (2nd advisor-review finding): _empty_filter_warning's
        'warning' key on an otherwise-'success' report (every row for that
        report was filtered out by the tenant-membership filter -- usually a
        sign the identity column didn't actually match) is the same shape of
        "nothing failed, but something's still worth a second look" signal
        as the ora2-skipped case above. It must land in
        manifest['courses_needing_review'], and must NOT be counted as a
        course-level error (manifest['status'] stays plain 'complete').
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        course = CourseOverviewFactory()
        CourseEnrollmentFactory(user=member, course_id=course.id, is_active=True)

        output_dir = tempfile.mkdtemp()
        out = StringIO()

        def warned_process_course(self, course_id, *args, **kwargs):
            return {
                'grades': {
                    'status': 'success', 'rows': 0,
                    'warning': 'all 3 row(s) were filtered out -- check the identity column',
                },
            }

        with patch.object(Command, '_process_course', warned_process_course):
            call_command(
                'export_tenant_reports_csv', sub_org.slug, '--reports', 'grades',
                '--output-dir', output_dir, stdout=out,
            )

        with open(os.path.join(output_dir, 'MANIFEST.json')) as f:
            manifest = json.load(f)
        assert manifest['courses_needing_review'] == [str(course.id)]
        assert manifest['courses_with_errors'] == []
        assert manifest['status'] == 'complete'
        assert 'Exported tenant CSV reports' not in out.getvalue()
        assert 'warning or skipped report' in out.getvalue()
