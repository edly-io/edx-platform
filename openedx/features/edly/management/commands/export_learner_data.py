"""
Export one Edly tenant's learner data to a portable JSON bundle.

Koa counterpart of the Ulmo-side `export_learner_data` command that lives in
the `edlysaas-data-migrations` plugin. That plugin cannot be installed on Koa
(it pins Python >=3.11 / Django 4.2-5.2), so this is a self-contained, native
edx-platform management command -- no dependency on the plugin, reading
Koa's own `default` database directly.

Backs up exactly what an off-boarding pause/resume client needs restored:
accounts/profiles, tenant membership, enrollment status, grades, certificates,
and course progress. Course content is already handled by the native
`export_all_courses` management command (note: that command is unscoped --
it exports ALL courses, not tenant-filtered). WordPress data is out of scope
here.

Output bundle shape matches the Ulmo-side `import_learner_data` command
exactly, so a bundle produced here can be handed to that command on restore:
    <bundle_dir>/MANIFEST.json
    <bundle_dir>/learners/<table>.json   # {"columns": [...], "rows": [[...], ...]}

One deliberate exception to the "columns pass through unchanged" rule: Koa's
tenant-membership table is `edly_edlymultisiteaccess` (column `sub_org_id`),
while the Ulmo-side importer expects `edly_features_app_edlymultisiteaccess`
(column `tenant_id`). This command emits that table under the Ulmo-side
filename with the column renamed -- it does NOT remap the column's *value*,
since Koa's sub_org primary key lives in a different id-space than any Ulmo
tenant id. Reconciling that value is a documented follow-up on the Ulmo
import side, not something this export command attempts to solve.

`auth_user_groups.group_id` has the identical problem and is flagged here for
the same reason: Koa's Django auth group ids live in their own id-space, so a
restored `group_id` value won't necessarily point at the matching group on
the Ulmo side either. As with `tenant_id` above, this command does not
attempt to remap it -- only the target Ulmo instance knows its own id-space
-- it is called out here as the same class of known follow-up for the Ulmo
import side to reconcile. (Contrast with `auth_userprofile.allow_certificate`
below, which is a column Ulmo's schema drops entirely rather than an
id-space mismatch, and so is stripped from this export instead of deferred.)

Usage (in the LMS):
    python manage.py export_learner_data <slug> --dry-run
    python manage.py export_learner_data <slug>
    python manage.py export_learner_data <slug> --output-dir /tmp/my-bundle
"""

import json
import os
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone
from six import text_type

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from openedx.features.edly.models import EdlyMultiSiteAccess, EdlySubOrganization
from student.models import CourseEnrollment

# Candidate names for the "which course does this row belong to" column --
# name differs per table/Django version, so it's detected at runtime via
# information_schema rather than hardcoded per table.
COURSE_COL_CANDIDATES = ('course_id', 'context_key', 'course_key')

# (table, user_col, needs_course_filter). user_col is the column holding the
# learner's id ("id" for auth_user itself). needs_course_filter additionally
# scopes by the table's course column -- both filters together give "this
# tenant's own users' activity in this tenant's own courses", even though
# users and course rows are otherwise shared platform-wide.
TABLE_SCOPE = [
    ('auth_user', 'id', False),
    ('auth_userprofile', 'user_id', False),
    ('auth_registration', 'user_id', False),
    ('auth_user_groups', 'user_id', False),
    ('student_anonymoususerid', 'user_id', False),
    ('student_courseenrollment', 'user_id', True),
    ('courseware_studentmodule', 'student_id', True),
    ('completion_blockcompletion', 'user_id', True),
    ('certificates_generatedcertificate', 'user_id', True),
    ('grades_persistentcoursegrade', 'user_id', True),
    ('grades_persistentsubsectiongrade', 'user_id', True),
]

# No direct user/course column -- scoped via student_courseenrollment.id
# instead (their only course linkage is the enrollment FK, not a course
# column of their own).
ENROLLMENT_LINKED_TABLES = ['student_courseenrollmentattribute', 'student_manualenrollmentaudit']

ALL_TABLES = [table for table, _, _ in TABLE_SCOPE] + ENROLLMENT_LINKED_TABLES

# Koa's tenant-membership table. Handled separately from TABLE_SCOPE (see
# `_fetch_membership_table`) because its output filename/column must match
# the Ulmo-side importer's expectations, not Koa's own names.
MEMBERSHIP_TABLE = 'edly_edlymultisiteaccess'
MEMBERSHIP_TARGET_NAME = 'edly_features_app_edlymultisiteaccess'
MEMBERSHIP_COLUMN_RENAME = {'sub_org_id': 'tenant_id'}

# `auth_userprofile.allow_certificate` exists on Koa but Ulmo's schema drops
# it entirely (see edlysaas-data-migrations/CLAUDE.md's column-transform
# notes). The Ulmo-side importer inserts every bundle column verbatim, so
# leaving this column in would crash that INSERT. Stripped the same way the
# membership table's sub_org_id -> tenant_id rename is applied above -- from
# both the emitted `columns` list and every row's values (see
# `_strip_columns`) -- rather than deferred as a follow-up, since unlike
# `tenant_id`/`group_id` this isn't an id-space problem the import side is
# better positioned to solve.
USERPROFILE_TABLE = 'auth_userprofile'
USERPROFILE_DROPPED_COLUMNS = ('allow_certificate',)

MANIFEST_FILENAME = 'MANIFEST.json'


def bt(col):
    """
    Backtick-quote a column/table identifier so reserved words (e.g. `key`) don't break SQL.
    """
    return u'`{0}`'.format(col)


class Command(BaseCommand):
    """
    Export one Edly tenant's learner data (accounts, profiles, tenant
    membership, enrollment status, grades, certificates, and course
    progress) to a portable JSON bundle.
    """
    help = (
        "Export one Edly tenant's learner data to a portable JSON bundle "
        "for off-boarding pause/resume."
    )

    def add_arguments(self, parser):
        """
        Add command line arguments.
        """
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print resolved orgs/courses/users and per-table row counts; write no files.',
        )
        parser.add_argument(
            '--output-dir',
            default=None,
            help='Bundle directory to write into (default: /tmp/edm_exports/<slug>_<timestamp>/).',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Rows fetched per SELECT batch (default: 1000). Keeps large tables '
                 '(e.g. courseware_studentmodule) from being loaded into memory all at once.',
        )

    def handle(self, *args, **options):
        """
        Resolve the tenant's orgs/courses/users and export the learner tables.
        """
        slug = options['slug']
        dry_run = options['dry_run']
        self.batch_size = options['batch_size']

        self._print_header(slug, dry_run)

        sub_org = self._get_sub_org(slug)
        orgs = self._resolve_orgs(sub_org)
        if not orgs:
            raise CommandError(u"No course_org_filter configured for tenant '{0}'.".format(slug))

        course_ids = self._get_course_ids(orgs)
        user_ids = self._get_user_ids(sub_org, course_ids)

        self.stdout.write(u"Orgs: {0}".format(orgs))
        self.stdout.write(u"Courses resolved: {0}".format(len(course_ids)))
        self.stdout.write(u"Users resolved: {0}".format(len(user_ids)))

        if not course_ids and user_ids:
            self.stdout.write(self.style.WARNING(
                u"No courses resolved for this tenant's orgs (check course_org_filter) -- "
                u"course-scoped tables (enrollments, grades, certificates, progress) will "
                u"export 0 rows even though {0} user(s) were found.".format(len(user_ids))
            ))

        if not user_ids:
            self.stdout.write(self.style.WARNING("No users found for this tenant -- nothing to export."))
            return

        if dry_run:
            self._dry_run(user_ids, course_ids)
            return

        timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
        bundle_dir = self._resolve_bundle_dir(slug, options.get('output_dir'), timestamp)
        self._make_private_dir(bundle_dir)
        learners_dir = os.path.join(bundle_dir, 'learners')
        self._make_private_dir(learners_dir)

        counts = {}
        enrollment_ids = []

        for table, user_col, needs_course_filter in TABLE_SCOPE:
            columns, rows = self._fetch_table(table, user_col, user_ids, course_ids if needs_course_filter else None)
            if table == USERPROFILE_TABLE:
                columns, rows = self._strip_columns(columns, rows, USERPROFILE_DROPPED_COLUMNS)
            if table == 'student_courseenrollment' and 'id' in columns:
                rows = self._capture_column(rows, columns.index('id'), enrollment_ids)
            row_count = self._write_table_json(os.path.join(learners_dir, u"{0}.json".format(table)), columns, rows)
            counts[table] = row_count
            self.stdout.write(u"  {0}: {1} rows".format(table, row_count))

        for table in ENROLLMENT_LINKED_TABLES:
            columns, rows = self._fetch_by_enrollment_ids(table, enrollment_ids)
            row_count = self._write_table_json(os.path.join(learners_dir, u"{0}.json".format(table)), columns, rows)
            counts[table] = row_count
            self.stdout.write(u"  {0}: {1} rows".format(table, row_count))

        columns, rows = self._fetch_membership_table(user_ids)
        row_count = self._write_table_json(
            os.path.join(learners_dir, u"{0}.json".format(MEMBERSHIP_TARGET_NAME)), columns, rows
        )
        counts[MEMBERSHIP_TARGET_NAME] = row_count
        self.stdout.write(u"  {0}: {1} rows".format(MEMBERSHIP_TARGET_NAME, row_count))

        self._write_manifest(bundle_dir, slug, counts)

        self.stdout.write("\n" + "=" * 72)
        self.stdout.write(u"Exported learner data for {0} user(s) to {1}".format(len(user_ids), learners_dir))
        self.stdout.write("=" * 72)

    # ------------------------------------------------------------------ tenant scoping

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
        Resolve the tenant's course_org_filter.

        Primary: SiteConfiguration.get_value('course_org_filter') off the
        tenant's lms_site (the canonical Koa path for resolving a tenant's
        orgs). Fallback/cross-check: the sub_org's own edx_organizations M2M,
        for tenants whose site configuration doesn't set course_org_filter.
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

    def _get_course_ids(self, orgs):
        """
        Resolve course ids (as strings) for the tenant's orgs via CourseOverview.
        """
        return [
            text_type(course_id)
            for course_id in CourseOverview.objects.filter(org__in=orgs).values_list('id', flat=True)
        ]

    def _get_user_ids(self, sub_org, course_ids):
        """
        Resolve the tenant's user ids.

        Primary: direct EdlyMultiSiteAccess membership. Fallback (empty
        multisite access): enrollment-derived users scoped to the tenant's
        resolved courses.
        """
        user_ids = set(EdlyMultiSiteAccess.objects.filter(sub_org=sub_org).values_list('user_id', flat=True))
        if user_ids or not course_ids:
            return user_ids

        return set(
            CourseEnrollment.objects.filter(course_id__in=course_ids).values_list('user_id', flat=True)
        )

    # ------------------------------------------------------------------ schema introspection

    def _columns(self, table):
        """
        Return the ordered column names of a table, detected via information_schema.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s "
                "ORDER BY ordinal_position",
                [table],
            )
            return [row[0] for row in cursor.fetchall()]

    def _course_col(self, table):
        """
        Detect which of COURSE_COL_CANDIDATES this table uses for course-scoping.
        """
        cols = set(self._columns(table))
        for candidate in COURSE_COL_CANDIDATES:
            if candidate in cols:
                return candidate
        raise CommandError(u"No course-scoping column found on {0} (checked {1}).".format(table, COURSE_COL_CANDIDATES))

    # ------------------------------------------------------------------ table fetch

    def _fetch_table(self, table, user_col, user_ids, course_ids):
        """
        Fetch a table's rows scoped by user_ids and, when requested, course_ids.

        Returns (columns, rows) where `rows` is a lazy generator (see
        `_paginate`) -- callers should stream it (e.g. via `_write_table_json`)
        rather than materializing it with list()/len().
        """
        columns = self._columns(table)
        user_placeholders = ', '.join(['%s'] * len(user_ids))
        where_sql = u"{0} IN ({1})".format(bt(user_col), user_placeholders)
        where_args = list(user_ids)

        if course_ids:
            course_col = self._course_col(table)
            course_placeholders = ', '.join(['%s'] * len(course_ids))
            where_sql += u" AND {0} IN ({1})".format(bt(course_col), course_placeholders)
            where_args += list(course_ids)
        elif course_ids == []:
            # needs_course_filter=True but tenant has zero resolved courses -- match nothing.
            return columns, iter(())

        return columns, self._paginate(table, columns, where_sql, where_args)

    def _fetch_by_enrollment_ids(self, table, enrollment_ids):
        """
        Fetch a table's rows scoped by student_courseenrollment.id via its enrollment_id FK.
        """
        columns = self._columns(table)
        if not enrollment_ids:
            return columns, iter(())
        placeholders = ', '.join(['%s'] * len(enrollment_ids))
        where_sql = u"{0} IN ({1})".format(bt('enrollment_id'), placeholders)
        return columns, self._paginate(table, columns, where_sql, list(enrollment_ids))

    def _fetch_membership_table(self, user_ids):
        """
        Fetch the tenant-membership table under the Ulmo-side importer's expected shape.

        Reads Koa's `edly_edlymultisiteaccess`, but emits its `sub_org_id`
        column under the name `tenant_id` (see module docstring) -- the
        VALUE is left untouched, only the column name is renamed.
        """
        if not user_ids:
            return [], iter(())
        real_columns = self._columns(MEMBERSHIP_TABLE)
        placeholders = ', '.join(['%s'] * len(user_ids))
        where_sql = u"{0} IN ({1})".format(bt('user_id'), placeholders)
        rows = self._paginate(MEMBERSHIP_TABLE, real_columns, where_sql, list(user_ids))
        output_columns = [MEMBERSHIP_COLUMN_RENAME.get(col, col) for col in real_columns]
        return output_columns, rows

    def _paginate(self, table, columns, where_sql, where_args):
        """
        Yield a table's matching rows one at a time, fetching `self.batch_size`
        rows per SELECT instead of a single `fetchall()` -- tables like
        `courseware_studentmodule` can be millions of rows with KB-sized
        `state` blobs, a real OOM risk for a one-shot fetch.

        Keyset-paginates by `id` (`id > last_id`) when the table has one,
        falling back to OFFSET otherwise. OFFSET pagination is non-atomic
        against concurrent writes: rows inserted/deleted ahead of the
        current offset while the export runs shift every later page,
        silently skipping or duplicating rows -- keyset has no such window.

        Unlike the companion Ulmo-side `_paginate` (edlysaas-data-migrations),
        which accumulates every batch into one Python list before a single
        `json.dumps`, this yields lazily so `_write_table_json` can stream
        each row straight to disk as it's fetched -- accumulating the whole
        table would still risk OOM at Koa's real tenant scale even with the
        SELECTs themselves batched.
        """
        columns_sql = ', '.join(bt(col) for col in columns)

        if 'id' in columns:
            id_idx = columns.index('id')
            last_id = None
            while True:
                batch_sql, batch_args = where_sql, list(where_args)
                if last_id is not None:
                    batch_sql += u" AND {0} > %s".format(bt('id'))
                    batch_args.append(last_id)
                with connection.cursor() as cursor:
                    cursor.execute(
                        u"SELECT {0} FROM {1} WHERE {2} ORDER BY {3} LIMIT %s".format(
                            columns_sql, bt(table), batch_sql, bt('id')
                        ),
                        batch_args + [self.batch_size],
                    )
                    batch = cursor.fetchall()
                if not batch:
                    return
                for row in batch:
                    yield list(row)
                last_id = batch[-1][id_idx]
        else:
            order_sql = ', '.join(bt(col) for col in columns)
            offset = 0
            while True:
                with connection.cursor() as cursor:
                    cursor.execute(
                        u"SELECT {0} FROM {1} WHERE {2} ORDER BY {3} LIMIT %s OFFSET %s".format(
                            columns_sql, bt(table), where_sql, order_sql
                        ),
                        list(where_args) + [self.batch_size, offset],
                    )
                    batch = cursor.fetchall()
                if not batch:
                    return
                for row in batch:
                    yield list(row)
                offset += self.batch_size

    def _strip_columns(self, columns, rows, drop):
        """
        Drop one or more columns (by name) from a table's emitted `columns`
        list and every row's values -- e.g. `auth_userprofile.allow_certificate`
        (see USERPROFILE_DROPPED_COLUMNS), a column Ulmo's schema removes
        entirely, so the Ulmo-side importer's verbatim-column INSERT doesn't
        choke on a column that no longer exists there. Same technique already
        used for the membership table's sub_org_id -> tenant_id rename above,
        just dropping instead of renaming.
        """
        drop_idxs = {columns.index(col) for col in drop if col in columns}
        if not drop_idxs:
            return columns, rows
        keep_idxs = [i for i in range(len(columns)) if i not in drop_idxs]
        new_columns = [columns[i] for i in keep_idxs]

        def _stripped():
            for row in rows:
                yield [row[i] for i in keep_idxs]

        return new_columns, _stripped()

    def _capture_column(self, rows, col_idx, sink):
        """
        Pass rows through unchanged while recording one column's values into
        `sink` as they stream past -- used to capture
        student_courseenrollment's ids for the ENROLLMENT_LINKED_TABLES
        lookup without materializing the whole table just to grab that one
        column.
        """
        for row in rows:
            sink.append(row[col_idx])
            yield row

    # ------------------------------------------------------------------ bundle output

    def _resolve_bundle_dir(self, slug, output_dir, timestamp):
        """
        `--output-dir` wins verbatim if given; otherwise EDM_EXPORT_DIR/<slug>_<timestamp>/.
        """
        if output_dir:
            return output_dir
        base = getattr(settings, 'EDM_EXPORT_DIR', '/tmp/edm_exports')
        return os.path.join(base, u"{0}_{1}".format(slug, timestamp))

    def _make_private_dir(self, path):
        """
        Create `path` (and any missing parents), then force 0700 perms on it.

        `os.makedirs`'s `mode` argument only applies to the leaf directory it
        creates -- any parent directories created along the way get the
        process umask's default (commonly 0755) -- and the leaf's own mode
        can itself still be loosened by the umask. So this creates with
        mode=0o700 as a first pass and then `chmod`s explicitly afterwards to
        guarantee the final bits regardless of umask. These directories hold
        the same PII as the files written into them (see
        `_open_bundle_file`): auth_user password hashes, auth_registration
        activation keys, full auth_userprofile PII -- they must never be
        briefly group/other-readable or world-traversable.
        """
        if not os.path.exists(path):
            os.makedirs(path, mode=0o700)
        os.chmod(path, 0o700)

    def _open_bundle_file(self, path):
        """
        Open a bundle output file for writing with 0600 perms from creation.

        Bundle files can contain auth_user password hashes, auth_registration
        activation keys, and full auth_userprofile PII -- creating via
        `os.open` with an explicit mode avoids the brief window a plain
        `open()` followed by a later `os.chmod()` would leave the file at
        the process umask's default (typically 0644).
        """
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        return os.fdopen(fd, 'w', encoding='utf-8')

    def _json_default(self, value):
        """
        json.dumps(default=...) hook for DB row values that aren't natively JSON-serializable.
        """
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return text_type(value)
        if isinstance(value, (bytes, bytearray)):
            return value.decode('utf-8', errors='replace')
        raise TypeError(u"Not JSON serializable: {0!r}".format(type(value)))

    def _write_table_json(self, path, columns, rows):
        """
        Stream one table's scoped rows to {"columns": [...], "rows": [[...], ...]}.

        Written by hand -- one row's `json.dumps` at a time -- rather than
        building the whole payload and calling `json.dumps(payload)` once,
        since `rows` may be a lazy generator (see `_paginate`) over a table
        with millions of rows; materializing it into one Python list first
        would defeat the point of batching the SELECTs. Returns the total
        row count written, since callers no longer have a materialized list
        to `len()` themselves.
        """
        row_count = 0
        with self._open_bundle_file(path) as output_file:
            output_file.write(u'{"columns": ')
            output_file.write(json.dumps(columns))
            output_file.write(u', "rows": [')
            for row in rows:
                if row_count:
                    output_file.write(u', ')
                output_file.write(json.dumps(row, default=self._json_default))
                row_count += 1
            output_file.write(u']}')
        return row_count

    def _schema_state(self):
        """
        Cheap, best-effort "how many migrations has this DB seen" fingerprint.
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM django_migrations")
                return {'edxapp_migrations_applied': cursor.fetchone()[0]}
        except Exception:  # pylint: disable=broad-except
            return {}

    def _release_line(self):
        """
        Return this checkout's RELEASE_LINE, falling back to the literal "koa".
        """
        try:
            from openedx.core.release import RELEASE_LINE
            return RELEASE_LINE
        except ImportError:
            return 'koa'

    def _write_manifest(self, bundle_dir, slug, counts):
        """
        Write MANIFEST.json in the exact shape the Ulmo-side import_learner_data command reads.
        """
        manifest = {
            'slug': slug,
            'release_line': self._release_line(),
            'schema_state': self._schema_state(),
            'components': {'learners': counts},
        }
        manifest_path = os.path.join(bundle_dir, MANIFEST_FILENAME)
        with self._open_bundle_file(manifest_path) as manifest_file:
            manifest_file.write(json.dumps(manifest, indent=2, default=self._json_default))

    # ------------------------------------------------------------------ dry run / reporting

    def _dry_run(self, user_ids, course_ids):
        """
        Print per-table row counts without writing any files.
        """
        for table, user_col, needs_course_filter in TABLE_SCOPE:
            where_sql = u"{0} IN ({1})".format(bt(user_col), ', '.join(['%s'] * len(user_ids)))
            where_args = list(user_ids)
            if needs_course_filter and course_ids:
                course_col = self._course_col(table)
                where_sql += u" AND {0} IN ({1})".format(bt(course_col), ', '.join(['%s'] * len(course_ids)))
                where_args += list(course_ids)
            elif needs_course_filter and not course_ids:
                self.stdout.write(u"  {0}: 0 rows (no resolved courses)".format(table))
                continue
            with connection.cursor() as cursor:
                cursor.execute(u"SELECT COUNT(*) FROM {0} WHERE {1}".format(bt(table), where_sql), where_args)
                count = cursor.fetchone()[0]
            self.stdout.write(u"  {0}: {1} rows".format(table, count))

        for table in ENROLLMENT_LINKED_TABLES:
            self.stdout.write(
                u"  {0}: depends on student_courseenrollment scope (resolved on real run)".format(table)
            )

        if user_ids:
            with connection.cursor() as cursor:
                cursor.execute(
                    u"SELECT COUNT(*) FROM {0} WHERE {1} IN ({2})".format(
                        bt(MEMBERSHIP_TABLE), bt('user_id'), ', '.join(['%s'] * len(user_ids))
                    ),
                    list(user_ids),
                )
                membership_count = cursor.fetchone()[0]
            self.stdout.write(u"  {0}: {1} rows".format(MEMBERSHIP_TARGET_NAME, membership_count))

        self.stdout.write(self.style.WARNING("\nDRY RUN -- no files written."))

    def _print_header(self, slug, dry_run):
        """
        Print the command's run header.
        """
        self.stdout.write("\n" + "=" * 72)
        self.stdout.write(u"Export learner data: {0}".format(slug) + ("  [DRY RUN]" if dry_run else ""))
        self.stdout.write(u"Batch size: {0}".format(self.batch_size))
        self.stdout.write("=" * 72)
