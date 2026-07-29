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
"""
import csv
import json
import os
import stat
import tempfile
from io import StringIO

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
            header = ['Student ID', 'Email', 'Username'] + ['Grade'] + GRADES_SUMMARY_COLUMNS[3:]
            rows = [[1, 'a@x.com', 'alice'] + ['0.9'] + ['honor', 'N/A', 'N', 'N', '', 'enrolled']]
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
            # Deliberately omits 'Certificate Type', one of GRADES_SUMMARY_COLUMNS.
            header = [
                'Student ID', 'Email', 'Username', 'Enrollment Track', 'Verification Status',
                'Certificate Eligible', 'Certificate Delivered', 'Enrollment Status',
            ]
            rows = [[1, 'a@x.com', 'alice', 'honor', 'N/A', 'N', 'N', 'enrolled']]
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
