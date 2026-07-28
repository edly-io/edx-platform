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
settings module, which this suite doesn't have. The multi-stage capture
chains (`_export_ora_chain`, the verify_student parent/child fetch) are
instead unit-tested by mocking `_fetch_by_ids`/`_fetch_by_any_id` directly,
which verifies the *wiring* (captured ids from stage N feed stage N+1's
query) independent of the underlying SQL, which `FetchByIdsTests`/
`PaginateTests` cover separately.
"""
import datetime
import hashlib
import json
import os
import stat
import tempfile
from decimal import Decimal
from io import StringIO

from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from mock import MagicMock, patch

from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory
from openedx.features.edly.management.commands import export_learner_data as mod
from openedx.features.edly.management.commands.export_learner_data import (
    ALL_TABLES,
    ENROLLMENT_LINKED_TABLES,
    MEMBERSHIP_TARGET_NAME,
    ORA_CHAIN_TABLES,
    STRING_CAST_TABLES,
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


# A single real UUID in both forms `_export_ora_chain` needs to bridge:
# `submissions_submission.uuid`'s raw MySQL storage (32-char hex, no
# hyphens -- Django's UUIDField.get_db_prep_value degrades to `value.hex`
# on any backend without native UUID support) versus the canonical 36-char
# hyphenated string ORA2's own CharField `submission_uuid` columns are
# populated with via the submissions API/DRF serializer. See
# `_hex_uuid_to_canonical` and the module docstring's "submission_uuid
# format mismatch" note.
SUBMISSION_UUID_HEX = '2b2a44f212344a1b8c1dabcdef123456'
SUBMISSION_UUID_CANONICAL = '2b2a44f2-1234-4a1b-8c1d-abcdef123456'


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
        scoped_tables = [table for table, _ in TABLE_SCOPE]
        assert 'edly_edlymultisiteaccess' not in scoped_tables
        assert MEMBERSHIP_TARGET_NAME not in scoped_tables

    def test_verify_student_child_table_not_in_table_scope(self):
        # B3 correction: the child table has no user_id column of its own
        # (see module docstring) and must not be fetched as a plain
        # TABLE_SCOPE entry -- it's chained separately in handle().
        scoped_tables = [table for table, _ in TABLE_SCOPE]
        assert 'verify_student_softwaresecurephotoverification' not in scoped_tables
        assert 'verify_student_photoverification' in scoped_tables


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
    Tests for Command._resolve_orgs (informational/dry-run display only --
    no longer gates or filters the export, see module docstring).
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
    Tests for Command._get_course_ids (informational/dry-run display only).
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
    Tests for Command._get_user_ids -- membership-only scoping (see module
    docstring). No `course_ids` argument anymore: there is no
    enrollment-derived fallback to gate on courses.
    """

    def test_uses_multisite_access_membership(self):
        sub_org = EdlySubOrganizationFactory()
        user = UserFactory()
        EdlyMultiSiteAccessFactory(user=user, sub_org=sub_org)
        command = _make_command()
        assert command._get_user_ids(sub_org) == {user.id}

    def test_no_members_returns_empty_set(self):
        sub_org = EdlySubOrganizationFactory()
        command = _make_command()
        assert command._get_user_ids(sub_org) == set()

    def test_enrolled_only_non_member_is_excluded(self):
        """
        S7: a user merely enrolled in one of the tenant's courses, with no
        EdlyMultiSiteAccess row, must NOT be swept into the export -- the
        old CourseEnrollment-derived fallback was a cross-tenant PII risk
        and has been removed entirely.
        """
        sub_org = EdlySubOrganizationFactory()
        course = CourseOverviewFactory()
        enrolled_only_user = UserFactory()
        CourseEnrollmentFactory(user=enrolled_only_user, course_id=course.id)
        command = _make_command()
        assert command._get_user_ids(sub_org) == set()

    def test_member_included_even_without_any_enrollment(self):
        """
        Membership alone is sufficient -- a member need not be enrolled in
        any course to be included (the inverse of the case above).
        """
        sub_org = EdlySubOrganizationFactory()
        member = UserFactory()
        EdlyMultiSiteAccessFactory(user=member, sub_org=sub_org)
        command = _make_command()
        assert command._get_user_ids(sub_org) == {member.id}


class FetchByIdsTests(TestCase):
    """
    Tests for Command._fetch_by_ids / _fetch_by_any_id's WHERE-clause
    construction -- the shared primitive behind every TABLE_SCOPE fetch, the
    enrollment-linked tables, the string-cast tables, and every stage of the
    ORA/verify_student/certificate/visible-blocks chains.

    These return a lazy generator (see `_paginate`), so tests must consume
    it (`list(rows)`) *inside* the `with patch(...)` block -- the
    generator's body (and thus its `connection.cursor()` calls) doesn't run
    until iterated.
    """

    def test_single_column_where_clause(self):
        command = _make_command()
        cursor = MagicMock()
        # One batch of rows, then an empty batch to end the keyset pagination loop.
        cursor.fetchall.side_effect = [[(1, 'alice')], []]
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn), \
                patch.object(command, '_columns', return_value=['id', 'username']):
            columns, rows = command._fetch_by_ids('auth_user', 'id', {1, 2})
            rows = list(rows)

        assert columns == ['id', 'username']
        assert rows == [[1, 'alice']]
        sql = cursor.execute.call_args[0][0]
        assert 'IN (' in sql

    def test_empty_ids_short_circuits_without_querying(self):
        command = _make_command()
        with patch.object(command, '_columns', return_value=['id', 'user_id']):
            columns, rows = command._fetch_by_ids('some_table', 'user_id', [])
        assert columns == ['id', 'user_id']
        assert list(rows) == []

    def test_any_id_combines_clauses_with_or(self):
        command = _make_command()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn), \
                patch.object(command, '_columns', return_value=['id', 'submission_uuid', 'scorer_id']):
            _, rows = command._fetch_by_any_id(
                'assessment_assessment', [('submission_uuid', [SUBMISSION_UUID_CANONICAL]), ('scorer_id', ['anon-1'])]
            )
            list(rows)

        sql = cursor.execute.call_args[0][0]
        assert 'submission_uuid' in sql
        assert 'scorer_id' in sql
        assert ' OR ' in sql

    def test_any_id_skips_empty_pairs(self):
        command = _make_command()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(cursor)

        with patch.object(mod, 'connection', conn), \
                patch.object(command, '_columns', return_value=['id', 'submission_uuid', 'scorer_id']):
            _, rows = command._fetch_by_any_id(
                'assessment_assessment', [('submission_uuid', [SUBMISSION_UUID_CANONICAL]), ('scorer_id', [])]
            )
            list(rows)

        # `scorer_id` legitimately appears in the SELECT column list
        # regardless of the WHERE clause -- the real assertion is that it's
        # absent from the WHERE clause specifically.
        sql = cursor.execute.call_args[0][0]
        where_clause = sql.split(' WHERE ', 1)[1]
        assert 'submission_uuid' in where_clause
        assert 'scorer_id' not in where_clause
        assert ' OR ' not in where_clause

    def test_any_id_all_pairs_empty_short_circuits(self):
        command = _make_command()
        with patch.object(command, '_columns', return_value=['id', 'submission_uuid', 'scorer_id']):
            columns, rows = command._fetch_by_any_id(
                'assessment_assessment', [('submission_uuid', []), ('scorer_id', [])]
            )
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


class ExportOraChainTests(TestCase):
    """
    Tests for Command._export_ora_chain's staged capture-then-fetch wiring
    (B1) -- verifies that ids captured while writing one stage are used to
    query the next, without needing a real SQL backend. SQL-building itself
    is covered separately by FetchByIdsTests.
    """

    def test_full_chain_wires_captured_ids_between_stages(self):
        command = _make_command()
        learners_dir = tempfile.mkdtemp()
        counts, checksums = {}, {}

        def fake_fetch_by_ids(table, column, ids):
            if table == 'submissions_studentitem':
                assert column == 'student_id' and list(ids) == ['anon-1']
                return ['id', 'student_id'], iter([[501, 'anon-1']])
            if table == 'submissions_submission':
                assert column == 'student_item_id' and list(ids) == [501]
                # The raw DB value is hex, no hyphens (see SUBMISSION_UUID_HEX
                # docstring) -- _export_ora_chain's real (unmocked)
                # _capture_column/transform call must convert this to
                # SUBMISSION_UUID_CANONICAL before using it below.
                return (
                    ['id', 'uuid', 'student_item_id', 'team_submission_id'],
                    iter([[601, SUBMISSION_UUID_HEX, 501, None]]),
                )
            if table == 'submissions_score':
                assert column == 'student_item_id' and list(ids) == [501]
                return ['id', 'student_item_id'], iter([[701, 501]])
            if table == 'submissions_scoresummary':
                assert column == 'student_item_id' and list(ids) == [501]
                return ['id', 'student_item_id'], iter(())
            if table == 'submissions_scoreannotation':
                assert column == 'score_id' and list(ids) == [701]
                return ['id', 'score_id'], iter(())
            if table == 'submissions_teamsubmission':
                # team_submission_id was None on the one submission row --
                # must be filtered out before reaching this fetch.
                assert column == 'id' and list(ids) == []
                return ['id'], iter(())
            if table == 'assessment_assessmentpart':
                assert column == 'assessment_id' and list(ids) == [801]
                return ['id', 'assessment_id'], iter(())
            if table == 'assessment_assessmentfeedback':
                assert column == 'submission_uuid' and list(ids) == [SUBMISSION_UUID_CANONICAL]
                return ['id', 'submission_uuid'], iter(())
            if table == 'assessment_teamstaffworkflow':
                assert column == 'staffworkflow_ptr_id' and list(ids) == [901]
                return ['staffworkflow_ptr_id'], iter(())
            if table == 'assessment_studenttrainingworkflowitem':
                assert column == 'workflow_id' and list(ids) == [1001]
                return ['id', 'workflow_id'], iter(())
            if table == 'assessment_sharedfileupload':
                assert column == 'owner_id' and list(ids) == ['anon-1']
                return ['id', 'owner_id'], iter(())
            raise AssertionError('unexpected _fetch_by_ids table: {0}'.format(table))

        def fake_fetch_by_any_id(table, column_ids_pairs):
            pairs = dict(column_ids_pairs)
            if table == 'assessment_assessment':
                assert pairs == {'submission_uuid': [SUBMISSION_UUID_CANONICAL], 'scorer_id': ['anon-1']}
                return ['id', 'submission_uuid', 'scorer_id'], iter([[801, SUBMISSION_UUID_CANONICAL, 'anon-1']])
            if table == 'assessment_peerworkflow':
                assert pairs == {'submission_uuid': [SUBMISSION_UUID_CANONICAL], 'student_id': ['anon-1']}
                return ['id'], iter(())
            if table == 'assessment_peerworkflowitem':
                # scorer_id/author_id here are peerworkflow_ids (empty, since
                # no peerworkflow rows were captured above), NOT anon_ids.
                assert pairs == {'scorer_id': [], 'author_id': []}
                return ['id'], iter(())
            if table == 'assessment_staffworkflow':
                assert pairs == {'submission_uuid': [SUBMISSION_UUID_CANONICAL], 'scorer_id': ['anon-1']}
                return ['id'], iter([[901]])
            if table == 'assessment_studenttrainingworkflow':
                assert pairs == {'submission_uuid': [SUBMISSION_UUID_CANONICAL], 'student_id': ['anon-1']}
                return ['id'], iter([[1001]])
            raise AssertionError('unexpected _fetch_by_any_id table: {0}'.format(table))

        with patch.object(command, '_fetch_by_ids', side_effect=fake_fetch_by_ids), \
                patch.object(command, '_fetch_by_any_id', side_effect=fake_fetch_by_any_id):
            command._export_ora_chain(learners_dir, ['anon-1'], counts, checksums)

        assert counts['submissions_studentitem'] == 1
        assert counts['submissions_submission'] == 1
        assert counts['assessment_assessment'] == 1
        assert counts['assessment_staffworkflow'] == 1
        assert counts['assessment_studenttrainingworkflow'] == 1
        # every table in the documented chain got fetched and written, even
        # the ones that came back empty above.
        for table in ORA_CHAIN_TABLES:
            assert table in counts, table
            assert table in checksums, table

    def test_submission_uuid_hex_to_canonical_conversion_matches_assessment_rows(self):
        """
        Regression test for the submission_uuid format mismatch: without
        `_hex_uuid_to_canonical`, this would have been a silent 0-row bug
        for assessment_assessmentfeedback (matched ONLY by submission_uuid,
        no scorer_id/student_id fallback) on every tenant, forever -- the
        raw hex value captured off submissions_submission.uuid would never
        equal the canonical hyphenated string ORA2 actually stores in
        assessment_assessmentfeedback.submission_uuid.
        """
        command = _make_command()
        learners_dir = tempfile.mkdtemp()
        counts, checksums = {}, {}
        captured_feedback_ids = []

        def fake_fetch_by_ids(table, column, ids):
            if table == 'submissions_studentitem':
                return ['id', 'student_id'], iter([[501, 'anon-1']])
            if table == 'submissions_submission':
                # Raw MySQL storage: 32-char hex, no hyphens.
                return (
                    ['id', 'uuid', 'student_item_id', 'team_submission_id'],
                    iter([[601, SUBMISSION_UUID_HEX, 501, None]]),
                )
            if table == 'assessment_assessmentfeedback':
                # This is the crux: if the caller passed the raw hex value
                # instead of the canonical form, this assert fails, proving
                # the match would never have happened in production either.
                captured_feedback_ids.extend(ids)
                assert list(ids) == [SUBMISSION_UUID_CANONICAL], (
                    "assessment_assessmentfeedback must be queried with the "
                    "canonical hyphenated uuid, not the raw hex form"
                )
                return ['id', 'submission_uuid'], iter([[1, SUBMISSION_UUID_CANONICAL]])
            return ['id'], iter(())

        with patch.object(command, '_fetch_by_ids', side_effect=fake_fetch_by_ids), \
                patch.object(command, '_fetch_by_any_id', return_value=(['id'], iter(()))):
            command._export_ora_chain(learners_dir, ['anon-1'], counts, checksums)

        assert captured_feedback_ids == [SUBMISSION_UUID_CANONICAL]
        assert counts['assessment_assessmentfeedback'] == 1

        # The bundle file itself must keep the RAW hex value, unchanged --
        # only the in-memory matching list is converted (see
        # _capture_column's `transform` argument and the module docstring).
        with open(os.path.join(learners_dir, 'submissions_submission.json')) as f:
            payload = json.load(f)
        assert payload['rows'] == [[601, SUBMISSION_UUID_HEX, 501, None]]

    def test_empty_anon_ids_yields_zero_rows_everywhere(self):
        command = _make_command()
        learners_dir = tempfile.mkdtemp()
        counts, checksums = {}, {}

        with patch.object(command, '_columns', return_value=['id']):
            command._export_ora_chain(learners_dir, [], counts, checksums)

        for table in ORA_CHAIN_TABLES:
            assert counts[table] == 0


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
                patch('builtins.open') as mock_open:
            command._dry_run({1, 2})

        mock_open.assert_not_called()

    def test_dry_run_lists_deferred_chain_tables(self):
        command = _make_command()
        count_cursor = MagicMock()
        count_cursor.fetchone.return_value = (0,)
        conn = MagicMock()
        conn.cursor.side_effect = lambda: _cursor_ctx(count_cursor)

        with patch.object(mod, 'connection', conn):
            command._dry_run({1})

        output = command.stdout.getvalue()
        for table in ENROLLMENT_LINKED_TABLES + ORA_CHAIN_TABLES:
            assert table in output


class HandleWritesBundleTests(TestCase):
    """
    Tests that a real (non-dry-run) run writes one JSON file per table plus
    MANIFEST.json, and that the new capture chains wire up correctly at the
    handle() orchestration level.
    """

    def test_writes_json_per_table_and_manifest(self):
        command = _make_command()
        bundle_dir = tempfile.mkdtemp()

        def fake_fetch_by_ids(table, column, ids):
            if table == 'student_courseenrollment':
                # Deliberately a course OUTSIDE the tenant's resolved orgs
                # (see assertion below) -- _fetch_by_ids takes no course
                # filter at all anymore, so this must still come back.
                return ['id', 'user_id', 'course_id'], iter([[101, 1, 'course-v1:other-org+C9+2026']])
            if table == 'auth_userprofile':
                # allow_certificate must be stripped by handle()'s B2 special-case before writing.
                return ['id', 'user_id', 'allow_certificate'], iter([[1, 1, True]])
            if table == 'student_anonymoususerid':
                return ['id', 'user_id', 'anonymous_user_id'], iter([[1, 1, 'anon-1']])
            if table == 'certificates_generatedcertificate':
                return ['id', 'user_id'], iter([[301, 1]])
            if table == 'grades_persistentsubsectiongrade':
                return ['id', 'user_id', 'visible_blocks_hash'], iter([[401, 1, 'hash-1']])
            if table == 'verify_student_photoverification':
                return ['id', 'user_id'], iter([[901, 1]])
            if table in ENROLLMENT_LINKED_TABLES:
                return ['id', 'enrollment_id'], iter(())
            if table in [t for t, _ in STRING_CAST_TABLES]:
                return ['id', 'user'], iter(())
            if table == 'verify_student_softwaresecurephotoverification':
                assert list(ids) == [901]
                return ['photoverification_ptr_id'], iter(())
            if table == 'certificates_certificateinvalidation':
                assert list(ids) == [301]
                return ['id', 'generated_certificate_id'], iter(())
            if table == 'grades_visibleblocks':
                assert list(ids) == ['hash-1']
                return ['hashed'], iter(())
            return ['id', 'user_id'], iter([[1, 1]])

        with patch.object(command, '_get_sub_org', return_value=MagicMock()), \
                patch.object(command, '_resolve_orgs', return_value=['tenant-org']), \
                patch.object(command, '_get_course_ids', return_value=['course-v1:tenant-org+C1+2026']), \
                patch.object(command, '_get_user_ids', return_value={1}), \
                patch.object(command, '_fetch_by_ids', side_effect=fake_fetch_by_ids), \
                patch.object(command, '_fetch_by_any_id', return_value=(['id'], iter(()))), \
                patch.object(
                    command, '_fetch_membership_table', return_value=(['id', 'user_id', 'tenant_id'], iter(()))
                ), \
                patch.object(command, '_schema_state', return_value={}):
            command.handle(slug='acme', dry_run=False, output_dir=bundle_dir, batch_size=1000)

        learners_dir = bundle_dir + '/learners'
        with open(learners_dir + '/auth_user.json') as f:
            payload = json.load(f)
        assert payload == {'columns': ['id', 'user_id'], 'rows': [[1, 1]]}

        # S4/S7: a member's enrollment in a course OUTSIDE the tenant's
        # resolved orgs is included -- no course-org filtering applies to
        # course-scoped tables anymore.
        with open(learners_dir + '/student_courseenrollment.json') as f:
            payload = json.load(f)
        assert payload['rows'] == [[101, 1, 'course-v1:other-org+C9+2026']]

        # B2: allow_certificate is stripped from both columns and row values end-to-end.
        with open(learners_dir + '/auth_userprofile.json') as f:
            payload = json.load(f)
        assert payload == {'columns': ['id', 'user_id'], 'rows': [[1, 1]]}

        with open(bundle_dir + '/MANIFEST.json') as f:
            manifest = json.load(f)
        assert manifest['slug'] == 'acme'
        assert manifest['release_line'] == 'koa'
        assert manifest['components']['learners']['student_courseenrollment'] == 1
        # N2: checksums recorded as a sibling key, not folded into components.
        assert 'checksums' in manifest
        assert manifest['checksums']['learners']['student_courseenrollment']
        assert isinstance(manifest['components']['learners']['student_courseenrollment'], int)

        # Every table this command knows about got a manifest entry.
        for table in ALL_TABLES:
            assert table in manifest['components']['learners'], table
            assert table in manifest['checksums']['learners'], table

        # S1: bundle directories are 0700, bundle files are 0600.
        assert stat.S_IMODE(os.stat(bundle_dir).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(learners_dir).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(learners_dir + '/auth_user.json').st_mode) == 0o600
        assert stat.S_IMODE(os.stat(bundle_dir + '/MANIFEST.json').st_mode) == 0o600

    def test_string_cast_tables_receive_str_user_ids(self):
        command = _make_command()
        bundle_dir = tempfile.mkdtemp()
        seen_ids = {}

        def fake_fetch_by_ids(table, column, ids):
            if table in [t for t, _ in STRING_CAST_TABLES]:
                seen_ids[table] = list(ids)
                return ['id', column], iter(())
            return ['id'], iter(())

        with patch.object(command, '_get_sub_org', return_value=MagicMock()), \
                patch.object(command, '_resolve_orgs', return_value=[]), \
                patch.object(command, '_get_course_ids', return_value=[]), \
                patch.object(command, '_get_user_ids', return_value={7}), \
                patch.object(command, '_fetch_by_ids', side_effect=fake_fetch_by_ids), \
                patch.object(command, '_fetch_by_any_id', return_value=(['id'], iter(()))), \
                patch.object(command, '_fetch_membership_table', return_value=([], iter(()))), \
                patch.object(command, '_schema_state', return_value={}):
            command.handle(slug='acme', dry_run=False, output_dir=bundle_dir, batch_size=1000)

        for table, _ in STRING_CAST_TABLES:
            assert seen_ids[table] == ['7']
            assert all(isinstance(i, str) for i in seen_ids[table])


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
        """
        Also covers Django multi-table-inheritance child tables (e.g.
        verify_student_softwaresecurephotoverification,
        assessment_teamstaffworkflow), whose primary key is a `*_ptr_id`
        column rather than a plain `id` -- they fall back to this same path.
        """
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
    Tests for Command._write_table_json's streaming JSON writer + checksum (B1/S1/C3/N2).
    """

    def test_round_trips_multiple_batches(self):
        command = _make_command()
        path = os.path.join(tempfile.mkdtemp(), 'table.json')

        def row_generator():
            yield [1, 'alice']
            yield [2, 'bob']
            yield [3, 'carol']

        row_count, digest = command._write_table_json(path, ['id', 'name'], row_generator())

        assert row_count == 3
        assert isinstance(digest, str) and len(digest) == 64  # sha256 hex digest
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
        assert payload == {
            'columns': ['id', 'name'],
            'rows': [[1, 'alice'], [2, 'bob'], [3, 'carol']],
        }

    def test_digest_matches_sha256_of_written_bytes(self):
        command = _make_command()
        path = os.path.join(tempfile.mkdtemp(), 'table.json')

        _, digest = command._write_table_json(path, ['id'], iter([[1], [2]]))

        with open(path, 'rb') as f:
            expected = hashlib.sha256(f.read()).hexdigest()
        assert digest == expected

    def test_empty_rows_produce_valid_json(self):
        command = _make_command()
        path = os.path.join(tempfile.mkdtemp(), 'empty.json')

        row_count, digest = command._write_table_json(path, ['id'], iter(()))

        assert row_count == 0
        assert digest
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


class ResolveBundleDirTests(TestCase):
    """
    Tests for Command._resolve_bundle_dir's fallback default (N3): a
    MEDIA_ROOT-based path rather than bare /tmp, while both existing
    override mechanisms (--output-dir, EDM_EXPORT_DIR) still win verbatim.
    """

    def test_output_dir_wins_verbatim(self):
        command = _make_command()
        assert command._resolve_bundle_dir('acme', '/custom/path', '20260101000000') == '/custom/path'

    @override_settings(EDM_EXPORT_DIR='/configured/export/dir')
    def test_edm_export_dir_setting_used_when_no_output_dir(self):
        command = _make_command()
        result = command._resolve_bundle_dir('acme', None, '20260101000000')
        assert result == '/configured/export/dir/acme_20260101000000'

    @override_settings(MEDIA_ROOT='/media')
    def test_falls_back_to_media_root_not_tmp_when_neither_set(self):
        # EDM_EXPORT_DIR is not a real Django setting anywhere in this
        # codebase (grepped requirements/settings -- it only exists as this
        # command's own getattr default), so it's genuinely absent here,
        # exercising the same "neither override configured" path a fresh
        # devstack would hit.
        command = _make_command()
        assert not hasattr(mod.settings, 'EDM_EXPORT_DIR')
        result = command._resolve_bundle_dir('acme', None, '20260101000000')
        assert result == os.path.join('/media', 'edm_exports', 'acme_20260101000000')
        assert not result.startswith('/tmp')


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
    Tests for Command._capture_column, used to grab ids/FK values while
    streaming (enrollment ids, anonymous user ids, certificate ids,
    visible-blocks hashes, every stage of the ORA chain, ...).
    """

    def test_records_column_values_while_passing_rows_through(self):
        command = _make_command()
        sink = []
        rows = iter([[101, 1], [102, 1]])

        result = list(command._capture_column(rows, 0, sink))

        assert result == [[101, 1], [102, 1]]
        assert sink == [101, 102]

    def test_records_none_values_too(self):
        # e.g. submissions_submission.team_submission_id, nullable -- the
        # caller is responsible for filtering Nones before using the sink.
        command = _make_command()
        sink = []
        rows = iter([[1, None], [2, 55]])

        list(command._capture_column(rows, 1, sink))

        assert sink == [None, 55]

    def test_transform_applies_to_sink_but_not_to_the_yielded_row(self):
        """
        Used for submission_uuids (see _hex_uuid_to_canonical): the sink
        needs a converted value for the next query, but the row passed
        onward -- and therefore what ends up written to the bundle file --
        must stay exactly as read from the DB.
        """
        command = _make_command()
        sink = []
        rows = iter([[601, SUBMISSION_UUID_HEX]])

        result = list(command._capture_column(rows, 1, sink, transform=mod._hex_uuid_to_canonical))

        assert result == [[601, SUBMISSION_UUID_HEX]]
        assert sink == [SUBMISSION_UUID_CANONICAL]


class HexUuidToCanonicalTests(TestCase):
    """
    Tests for the module-level _hex_uuid_to_canonical helper (see the
    module docstring's "submission_uuid format mismatch" note).
    """

    def test_converts_raw_hex_to_canonical_hyphenated_form(self):
        assert mod._hex_uuid_to_canonical(SUBMISSION_UUID_HEX) == SUBMISSION_UUID_CANONICAL

    def test_empty_value_passes_through_unchanged(self):
        assert mod._hex_uuid_to_canonical('') == ''
        assert mod._hex_uuid_to_canonical(None) is None
