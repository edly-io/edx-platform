"""
Tests for the export_learner_data management command.

Tenant-scoping (orgs/courses/users resolution) is exercised against the real
ORM (sqlite in test settings handles these fine, matching the pattern used
throughout openedx/features/edly/tests/). The raw-SQL table dump/dry-run/
bundle-writing paths are exercised with `connection.cursor()` mocked out,
since `information_schema`/`DATABASE()` introspection queries this command
relies on are MySQL-specific and don't behave the same way against sqlite.

Known gap: because of that, none of the mocked-cursor tests below run the
raw-SQL path against a real MySQL instance end-to-end -- the backticked
identifiers, `information_schema` introspection, and the keyset
`ORDER BY ... LIMIT` pagination SQL built in `_paginate` are never executed
for real. A MySQL-gated integration test would need a MySQL-backed test
settings module, which this suite doesn't have.
"""
import datetime
import json
import os
import stat
import tempfile
from decimal import Decimal
from io import StringIO

from django.core.management.base import CommandError
from django.test import TestCase
from mock import MagicMock, patch

from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory
from openedx.features.edly.management.commands import export_learner_data as mod
from openedx.features.edly.management.commands.export_learner_data import (
    ALL_TABLES,
    MEMBERSHIP_TARGET_NAME,
    TABLE_SCOPE,
    Command,
)
from openedx.features.edly.tests.factories import EdlyMultiSiteAccessFactory, EdlySubOrganizationFactory
from organizations.tests.factories import OrganizationFactory
from student.tests.factories import CourseEnrollmentFactory, UserFactory


def _cursor_ctx(cursor):
    """
    Build a MagicMock usable as `with connection.cursor() as cursor:`.
    """
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cursor)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _make_command():
    """
    Build a Command instance with stdout/style wired up for assertions.
    """
    command = Command()
    command.stdout = StringIO()
    command.stderr = StringIO()
    command.style = MagicMock()
    for attr in ('SUCCESS', 'ERROR', 'WARNING'):
        setattr(command.style, attr, lambda value: value)
    command.batch_size = 1000
    return command


class TableScopeTests(TestCase):
    """
    Sanity checks on the curated table lists.
    """

    def test_all_tables_has_no_duplicates(self):
        assert len(ALL_TABLES) == len(set(ALL_TABLES))

    def test_membership_table_not_in_table_scope(self):
        scoped_tables = [table for table, _, _ in TABLE_SCOPE]
        assert 'edly_edlymultisiteaccess' not in scoped_tables
        assert MEMBERSHIP_TARGET_NAME not in scoped_tables


class GetSubOrgTests(TestCase):
    """
    Tests for Command._get_sub_org.
    """

    def test_found(self):
        sub_org = EdlySubOrganizationFactory(slug='test-tenant')
        command = _make_command()
        assert command._get_sub_org('test-tenant') == sub_org

    def test_not_found_raises_command_error(self):
        command = _make_command()
        with self.assertRaises(CommandError):
            command._get_sub_org('does-not-exist')


class ResolveOrgsTests(TestCase):
    """
    Tests for Command._resolve_orgs.
    """

    def test_primary_path_uses_site_configuration_course_org_filter(self):
        sub_org = EdlySubOrganizationFactory()
        SiteConfigurationFactory(
            site=sub_org.lms_site,
            enabled=True,
            site_values={'course_org_filter': ['org-a', 'org-b']},
        )
        command = _make_command()
        assert command._resolve_orgs(sub_org) == ['org-a', 'org-b']

    def test_primary_path_wraps_single_string_org_filter(self):
        sub_org = EdlySubOrganizationFactory()
        SiteConfigurationFactory(
            site=sub_org.lms_site,
            enabled=True,
            site_values={'course_org_filter': 'org-a'},
        )
        command = _make_command()
        assert command._resolve_orgs(sub_org) == ['org-a']

    def test_falls_back_to_edx_organizations_when_no_site_configuration(self):
        edx_org = OrganizationFactory(short_name='fallback-org')
        sub_org = EdlySubOrganizationFactory(edx_organizations=[edx_org])
        command = _make_command()
        assert command._resolve_orgs(sub_org) == ['fallback-org']

    def test_falls_back_to_edx_organizations_when_course_org_filter_empty(self):
        edx_org = OrganizationFactory(short_name='fallback-org')
        sub_org = EdlySubOrganizationFactory(edx_organizations=[edx_org])
        SiteConfigurationFactory(site=sub_org.lms_site, enabled=True, site_values={})
        command = _make_command()
        assert command._resolve_orgs(sub_org) == ['fallback-org']


class GetCourseIdsTests(TestCase):
    """
    Tests for Command._get_course_ids.
    """

    def test_resolves_course_ids_as_strings_for_matching_orgs(self):
        course = CourseOverviewFactory(org='matched-org')
        CourseOverviewFactory(org='other-org')
        command = _make_command()
        course_ids = command._get_course_ids(['matched-org'])
        assert course_ids == [str(course.id)]

    def test_no_matching_orgs_returns_empty_list(self):
        CourseOverviewFactory(org='other-org')
        command = _make_command()
        assert command._get_course_ids(['no-such-org']) == []


class GetUserIdsTests(TestCase):
    """
    Tests for Command._get_user_ids.
    """

    def test_primary_path_uses_multisite_access(self):
        sub_org = EdlySubOrganizationFactory()
        user = UserFactory()
        EdlyMultiSiteAccessFactory(user=user, sub_org=sub_org)
        command = _make_command()
        assert command._get_user_ids(sub_org, []) == {user.id}

    def test_falls_back_to_enrollment_derived_users_when_membership_empty(self):
        sub_org = EdlySubOrganizationFactory()
        course = CourseOverviewFactory()
        enrolled_user = UserFactory()
        CourseEnrollmentFactory(user=enrolled_user, course_id=course.id)
        command = _make_command()
        assert command._get_user_ids(sub_org, [str(course.id)]) == {enrolled_user.id}

    def test_no_fallback_without_resolved_courses(self):
        sub_org = EdlySubOrganizationFactory()
        command = _make_command()
        assert command._get_user_ids(sub_org, []) == set()


class FetchTableTests(TestCase):
    """
    Tests for Command._fetch_table's WHERE-clause construction.

    `_fetch_table` now returns a lazy generator (see `_paginate`) instead of
    a materialized list, so these tests must consume it (`list(rows)`)
    *inside* the `with patch(...)` block -- the generator's body (and thus
    its `connection.cursor()` calls) doesn't run until iterated.
    """

    def test_user_only_where_clause(self):
        command = _make_command()
        cursor = MagicMock()
        # One batch of rows, then an empty batch to end the keyset pagination loop.
        cursor.fetchall.side_effect = [[(1, 'alice')], []]
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn), \
                patch.object(command, '_columns', return_value=['id', 'username']):
            columns, rows = command._fetch_table('auth_user', 'id', {1, 2}, None)
            rows = list(rows)

        assert columns == ['id', 'username']
        assert rows == [[1, 'alice']]
        sql = cursor.execute.call_args[0][0]
        assert 'course' not in sql.lower()

    def test_course_filter_added_when_course_ids_given(self):
        command = _make_command()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn), \
                patch.object(command, '_columns', return_value=['id', 'user_id', 'course_id']), \
                patch.object(command, '_course_col', return_value='course_id'):
            _, rows = command._fetch_table('student_courseenrollment', 'user_id', {1}, ['course-v1:acme+C1+2026'])
            list(rows)

        sql = cursor.execute.call_args[0][0]
        assert 'course_id' in sql

    def test_needs_course_filter_with_no_resolved_courses_short_circuits(self):
        command = _make_command()
        with patch.object(command, '_columns', return_value=['id', 'user_id', 'course_id']):
            columns, rows = command._fetch_table('student_courseenrollment', 'user_id', {1}, [])
        assert list(rows) == []


class FetchMembershipTableTests(TestCase):
    """
    Tests for Command._fetch_membership_table's column rename.
    """

    def test_renames_sub_org_id_to_tenant_id(self):
        command = _make_command()
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[(1, 5, 42)], []]
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn), \
                patch.object(command, '_columns', return_value=['id', 'user_id', 'sub_org_id']):
            columns, rows = command._fetch_membership_table({1})
            rows = list(rows)

        assert columns == ['id', 'user_id', 'tenant_id']
        # Only the column name changes -- the raw value (Koa's sub_org PK) passes through untouched.
        assert rows == [[1, 5, 42]]

    def test_no_users_returns_empty(self):
        command = _make_command()
        columns, rows = command._fetch_membership_table(set())
        assert columns == []
        assert list(rows) == []


class DryRunTests(TestCase):
    """
    Tests that --dry-run never writes files.
    """

    def test_dry_run_writes_no_files(self):
        command = _make_command()
        count_cursor = MagicMock()
        count_cursor.fetchone.return_value = (0,)
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(count_cursor)

        with patch.object(mod, 'connection', conn), \
                patch.object(Command, '_course_col', return_value='course_id'), \
                patch('builtins.open') as mock_open:
            command._dry_run({1, 2}, ['course-v1:acme+C1+2026'])

        mock_open.assert_not_called()


class HandleWritesBundleTests(TestCase):
    """
    Tests that a real (non-dry-run) run writes one JSON file per table plus MANIFEST.json.
    """

    def test_writes_json_per_table_and_manifest(self):
        command = _make_command()
        bundle_dir = tempfile.mkdtemp()

        def fake_fetch_table(table, user_col, user_ids, course_ids):
            if table == 'student_courseenrollment':
                return ['id', 'user_id', 'course_id'], iter([[101, 1, 'course-v1:acme+C1+2026']])
            if table == 'auth_userprofile':
                # allow_certificate must be stripped by handle()'s B2 special-case before writing.
                return ['id', 'user_id', 'allow_certificate'], iter([[1, 1, True]])
            return ['id', 'user_id'], iter([[1, 1]])

        with patch.object(command, '_get_sub_org', return_value=MagicMock()), \
                patch.object(command, '_resolve_orgs', return_value=['acme']), \
                patch.object(command, '_get_course_ids', return_value=['course-v1:acme+C1+2026']), \
                patch.object(command, '_get_user_ids', return_value={1}), \
                patch.object(command, '_fetch_table', side_effect=fake_fetch_table), \
                patch.object(command, '_fetch_by_enrollment_ids', return_value=(['id', 'enrollment_id'], iter(()))), \
                patch.object(
                    command, '_fetch_membership_table', return_value=(['id', 'user_id', 'tenant_id'], iter(()))
                ), \
                patch.object(command, '_schema_state', return_value={}):
            command.handle(slug='acme', dry_run=False, output_dir=bundle_dir, batch_size=1000)

        learners_dir = bundle_dir + '/learners'
        with open(learners_dir + '/auth_user.json') as f:
            payload = json.load(f)
        assert payload == {'columns': ['id', 'user_id'], 'rows': [[1, 1]]}

        with open(learners_dir + '/student_courseenrollment.json') as f:
            payload = json.load(f)
        assert payload['rows'] == [[101, 1, 'course-v1:acme+C1+2026']]

        # B2: allow_certificate is stripped from both columns and row values end-to-end.
        with open(learners_dir + '/auth_userprofile.json') as f:
            payload = json.load(f)
        assert payload == {'columns': ['id', 'user_id'], 'rows': [[1, 1]]}

        with open(bundle_dir + '/MANIFEST.json') as f:
            manifest = json.load(f)
        assert manifest['slug'] == 'acme'
        assert manifest['release_line'] == 'koa'
        assert manifest['components']['learners']['student_courseenrollment'] == 1

        # S1: bundle directories are 0700, bundle files are 0600.
        assert stat.S_IMODE(os.stat(bundle_dir).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(learners_dir).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(learners_dir + '/auth_user.json').st_mode) == 0o600
        assert stat.S_IMODE(os.stat(bundle_dir + '/MANIFEST.json').st_mode) == 0o600


class JsonDefaultTests(TestCase):
    """
    Tests for Command._json_default's handling of non-JSON-native values.
    """

    def test_datetime_and_date_are_isoformatted(self):
        command = _make_command()
        now = datetime.datetime(2026, 1, 1, 12, 0, 0)
        assert command._json_default(now) == now.isoformat()
        today = datetime.date(2026, 1, 1)
        assert command._json_default(today) == today.isoformat()

    def test_decimal_is_stringified(self):
        command = _make_command()
        assert command._json_default(Decimal('1.50')) == '1.50'

    def test_bytes_are_decoded(self):
        command = _make_command()
        assert command._json_default(b'hello') == 'hello'

    def test_unsupported_type_raises_type_error(self):
        command = _make_command()
        with self.assertRaises(TypeError):
            command._json_default(object())


class PaginateTests(TestCase):
    """
    Tests for Command._paginate's batched/keyset-pagination behavior (B1).
    """

    def test_keyset_pagination_fetches_multiple_batches_until_empty(self):
        command = _make_command()
        command.batch_size = 2
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [(1, 'a'), (2, 'b')],
            [(3, 'c')],
            [],
        ]
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn):
            rows = list(command._paginate('auth_user', ['id', 'username'], '1=1', []))

        assert rows == [[1, 'a'], [2, 'b'], [3, 'c']]
        assert cursor.execute.call_count == 3
        # The second batch's WHERE clause must key off the first batch's last id (2).
        second_call_sql, second_call_args = cursor.execute.call_args_list[1][0]
        assert 'id' in second_call_sql.lower()
        assert 2 in second_call_args

    def test_offset_fallback_when_no_id_column(self):
        command = _make_command()
        command.batch_size = 2
        cursor = MagicMock()
        cursor.fetchall.side_effect = [
            [('a',), ('b',)],
            [],
        ]
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn):
            rows = list(command._paginate('some_table', ['username'], '1=1', []))

        assert rows == [['a'], ['b']]
        first_call_sql = cursor.execute.call_args_list[0][0][0]
        assert 'OFFSET' in first_call_sql

    def test_no_rows_yields_nothing(self):
        command = _make_command()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn):
            rows = list(command._paginate('auth_user', ['id', 'username'], '1=1', []))

        assert rows == []
        assert cursor.execute.call_count == 1


class WriteTableJsonTests(TestCase):
    """
    Tests for Command._write_table_json's streaming JSON writer (B1/S1/C3).
    """

    def test_round_trips_multiple_batches(self):
        command = _make_command()
        path = os.path.join(tempfile.mkdtemp(), 'table.json')

        def row_generator():
            yield [1, 'alice']
            yield [2, 'bob']
            yield [3, 'carol']

        row_count = command._write_table_json(path, ['id', 'name'], row_generator())

        assert row_count == 3
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
        assert payload == {
            'columns': ['id', 'name'],
            'rows': [[1, 'alice'], [2, 'bob'], [3, 'carol']],
        }

    def test_empty_rows_produce_valid_json(self):
        command = _make_command()
        path = os.path.join(tempfile.mkdtemp(), 'empty.json')

        row_count = command._write_table_json(path, ['id'], iter(()))

        assert row_count == 0
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
        assert payload == {'columns': ['id'], 'rows': []}

    def test_writes_file_with_0600_permissions(self):
        command = _make_command()
        path = os.path.join(tempfile.mkdtemp(), 'perm.json')

        command._write_table_json(path, ['id'], iter(()))

        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


class MakePrivateDirTests(TestCase):
    """
    Tests for Command._make_private_dir's 0700 enforcement (S1).
    """

    def test_creates_new_dir_with_0700(self):
        command = _make_command()
        parent = tempfile.mkdtemp()
        target = os.path.join(parent, 'bundle')

        command._make_private_dir(target)

        assert os.path.isdir(target)
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700

    def test_forces_0700_on_pre_existing_dir(self):
        command = _make_command()
        target = tempfile.mkdtemp()
        os.chmod(target, 0o755)

        command._make_private_dir(target)

        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700


class StripColumnsTests(TestCase):
    """
    Tests for Command._strip_columns, used to drop auth_userprofile.allow_certificate (B2).
    """

    def test_drops_named_column_from_columns_and_rows(self):
        command = _make_command()
        columns = ['id', 'user_id', 'allow_certificate']
        rows = iter([[1, 10, True], [2, 20, False]])

        new_columns, new_rows = command._strip_columns(columns, rows, ('allow_certificate',))

        assert new_columns == ['id', 'user_id']
        assert list(new_rows) == [[1, 10], [2, 20]]

    def test_noop_when_column_absent(self):
        command = _make_command()
        columns = ['id', 'user_id']
        rows = iter([[1, 10]])

        new_columns, new_rows = command._strip_columns(columns, rows, ('allow_certificate',))

        assert new_columns == ['id', 'user_id']
        assert list(new_rows) == [[1, 10]]


class CaptureColumnTests(TestCase):
    """
    Tests for Command._capture_column, used to grab student_courseenrollment ids while streaming.
    """

    def test_records_column_values_while_passing_rows_through(self):
        command = _make_command()
        sink = []
        rows = iter([[101, 1], [102, 1]])

        result = list(command._capture_column(rows, 0, sink))

        assert result == [[101, 1], [102, 1]]
        assert sink == [101, 102]


class NoCoursesWarningTests(TestCase):
    """
    Tests for the C1 warning: course_ids resolves empty but the tenant still has members.
    """

    def test_warns_when_no_courses_resolved_but_users_exist(self):
        command = _make_command()
        bundle_dir = tempfile.mkdtemp()

        with patch.object(command, '_get_sub_org', return_value=MagicMock()), \
                patch.object(command, '_resolve_orgs', return_value=['acme']), \
                patch.object(command, '_get_course_ids', return_value=[]), \
                patch.object(command, '_get_user_ids', return_value={1}), \
                patch.object(command, '_fetch_table', return_value=(['id', 'user_id'], iter([[1, 1]]))), \
                patch.object(command, '_fetch_by_enrollment_ids', return_value=(['id', 'enrollment_id'], iter(()))), \
                patch.object(
                    command, '_fetch_membership_table', return_value=(['id', 'user_id', 'tenant_id'], iter(()))
                ), \
                patch.object(command, '_schema_state', return_value={}):
            command.handle(slug='acme', dry_run=False, output_dir=bundle_dir, batch_size=1000)

        output = command.stdout.getvalue()
        assert 'No courses resolved' in output
