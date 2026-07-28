"""
Export one Edly tenant's learner data to a portable JSON bundle.

Koa counterpart of the Ulmo-side `export_learner_data` command that lives in
the `edlysaas-data-migrations` plugin. That plugin cannot be installed on Koa
(it pins Python >=3.11 / Django 4.2-5.2), so this is a self-contained, native
edx-platform management command -- no dependency on the plugin, reading
Koa's own `default` database directly.

Backs up exactly what an off-boarding pause/resume client needs restored:
accounts/profiles, tenant membership, enrollment status, grades, certificates,
course progress, ORA/peer-assessment history, proctoring/ID-verification
records, and the other per-learner tables enumerated in TABLE_SCOPE and the
chains below. Course content is already handled by the native
`export_all_courses` management command (note: that command is unscoped --
it exports ALL courses, not tenant-filtered). WordPress data is out of scope
here.

Output bundle shape matches the Ulmo-side `import_learner_data` command
exactly, so a bundle produced here can be handed to that command on restore:
    <bundle_dir>/MANIFEST.json
    <bundle_dir>/learners/<table>.json   # {"columns": [...], "rows": [[...], ...]}

Tenant scoping -- membership only, not course-org filtering
=============================================================
This command scopes strictly by **tenant membership** (`EdlyMultiSiteAccess`),
matching the old production backup tool. Two things this deliberately does
NOT do, and why:

1. It does NOT fall back to `CourseEnrollment`-derived users when a tenant
   has no `EdlyMultiSiteAccess` rows. A user who merely enrolled in one of
   the tenant's courses is not a tenant member, and including them risked a
   cross-tenant PII leak (a learner from a *different* tenant who happens to
   share a course could be swept into this tenant's export). Membership is
   the only tenant boundary this command recognizes.
2. Once a user is confirmed to be a tenant member, it does NOT additionally
   restrict their course-scoped data (enrollments, grades, progress, ORA
   submissions, ...) to courses in the tenant's own `course_org_filter`.
   Members can be enrolled in courses outside their home tenant's orgs
   (cross-listed courses, legacy enrollments, etc.), and org-filtering was
   silently dropping that data from the backup. Course-scoped tables are
   therefore scoped by `user_id`/`student_id` alone, across every course the
   member is enrolled in.
   `_resolve_orgs`/`_get_course_ids` are still called and their results are
   still printed (operator visibility into how the tenant's
   `course_org_filter` is configured, and a dry-run sanity check), but
   neither gates the export nor filters any table's rows.

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

ORA/submissions chain (B1) -- what's included and what's deliberately not
==========================================================================
Open Response Assessment data is not keyed by `auth_user.id` at all: it is
reached via the anonymized ids captured from `student_anonymoususerid`
while streaming TABLE_SCOPE, then a chain of FKs through the `submissions`
and `assessment` (ORA2/openassessment) apps' own tables -- see
`_export_ora_chain` for the exact chain, verified against the real
`edx-submissions==3.2.2` and `edly-io/edx-ora2@develop-koa` models (the
version actually used at runtime/devstack per requirements/edx/base.txt and
development.txt; the separate requirements/edx/testing.txt used to run this
command's own test suite resolves plain upstream `ora2==2.11.5.1` instead --
the assessment-app schema referenced below holds under both). Deliberately
excluded from the export as reference/definition data rather than learner
data (a restored bundle assumes these already exist via course content
restore, same as course content itself being out of scope per above):
`assessment_rubric`, `assessment_criterion`, `assessment_criterionoption`,
`assessment_trainingexample`, `assessment_assessmentfeedbackoption`, and the
`assessment_assessmentfeedback` M2M through-tables (feedback<->assessments,
feedback<->options). `assessment_assessmentpart` itself IS exported, but it
FKs onward to `criterion`/`option` (not exported) -- restoring it assumes
those rows already exist on the target, the same kind of cross-table
dependency as the `tenant_id`/`group_id` id-space notes above.

`submission_uuid` format mismatch: `submissions_submission.uuid` is a plain
Django `UUIDField`. `UUIDField.get_db_prep_value` degrades to `value.hex`
(32-char, no hyphens) on any backend without native UUID support -- which
includes MySQL -- so this file's raw-SQL `_paginate` reads back that same
32-char hex form verbatim, bypassing the ORM deserialization that would
normally convert it to a `uuid.UUID` object. The `assessment_*` tables'
`submission_uuid` columns, by contrast, are plain `CharField`s populated by
ORA2's own application code via the submissions API/DRF serializer, which
always renders a UUID as the canonical 36-char hyphenated string (DRF's
`UUIDField.to_representation` calls `str(value)`). The two formats never
match in a raw `IN (...)` comparison. `_export_ora_chain` converts the
captured `submission_uuids` matching list via `_hex_uuid_to_canonical`
before using it to query onward -- but NOT the row data written to
`submissions_submission.json` itself, which stays in the raw hex form a
same-schema restore would need to write back verbatim (see
`_capture_column`'s `transform` argument).

Known scaling caveat: the anon-id/submission-uuid lists that seed the ORA
chain are accumulated in memory (unlike the per-table SELECTs, which are
batched via `_paginate`) and used directly as `IN (...)` binds. For a very
large, very active tenant this could approach SQL placeholder limits; this
command does not chunk those binds today.

Known gaps (reviewed but not implemented, table doesn't exist in this Koa
checkout -- verified against `requirements/edx/base.txt` rather than
assumed): `journal_djangoapp_journalmodel` (journal-xblock is not installed
here) and `problem_builder_answer` (problem-builder is not installed here).
Also out of scope: `proctoring_proctoredexamstudentattempthistory`,
`proctoring_proctoredexamstudentallowance{,history}` (proctoring tables
beyond the one attempt table named in the review).

`lti_consumer_ltiagsscore.user_id` is a plain `CharField`, not a real FK to
`auth_user` -- it is stringified the same way as the kwl CHAR-column case
below. Best-effort: rows written from an LTI-1.3 launch's
`external_user_id` (a separate id-space entirely) will not match and are
not exported by this command.

`kwl_djangoapp_kwlmodel.user` is a CHAR column storing a stringified user id
rather than an integer FK -- ids are cast to `str` before binding into that
table's `IN (...)` clause (see the `STRING_CAST_TABLES` loop in `handle`).

`verify_student_softwaresecurephotoverification` has no `user_id` column of
its own: `PhotoVerification` is a *concrete* base holding `user_id`, and
`SoftwareSecurePhotoVerification` extends it via Django multi-table
inheritance, so its table only carries its own fields plus a
`photoverification_ptr_id` FK back to the parent row. This command exports
both `verify_student_photoverification` (the real, user_id-scoped table)
and the child table (scoped via the parent's captured ids) -- see the
verify_student block in `handle`. `assessment_teamstaffworkflow` has the
identical multi-table-inheritance shape relative to
`assessment_staffworkflow` and is handled the same way.

Usage (in the LMS):
    python manage.py export_learner_data <slug> --dry-run
    python manage.py export_learner_data <slug>
    python manage.py export_learner_data <slug> --output-dir /tmp/my-bundle
"""

import hashlib
import json
import os
import uuid
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

# (table, user_col). user_col is the column holding the learner's id ("id"
# for auth_user itself). No table here is course-filtered -- see the module
# docstring's "Tenant scoping" section for why: once a user is confirmed to
# be a tenant member, every table below is scoped by that column alone,
# across all of that member's data platform-wide.
TABLE_SCOPE = [
    ('auth_user', 'id'),
    ('auth_userprofile', 'user_id'),
    ('auth_registration', 'user_id'),
    ('auth_user_groups', 'user_id'),
    ('student_anonymoususerid', 'user_id'),
    ('student_courseenrollment', 'user_id'),
    ('courseware_studentmodule', 'student_id'),
    ('completion_blockcompletion', 'user_id'),
    ('certificates_generatedcertificate', 'user_id'),
    ('grades_persistentcoursegrade', 'user_id'),
    ('grades_persistentsubsectiongrade', 'user_id'),

    # B2-B5: proctoring/verification/social-auth/edly-profile/access-role.
    ('social_auth_usersocialauth', 'user_id'),
    ('proctoring_proctoredexamstudentattempt', 'user_id'),
    ('verify_student_manualverification', 'user_id'),
    ('verify_student_ssoverification', 'user_id'),
    # verify_student_softwaresecurephotoverification is NOT here -- it has
    # no user_id column of its own (see module docstring). Its parent,
    # verify_student_photoverification, does, and is the real trivial add;
    # the child is fetched separately in handle() via the parent's ids.
    ('verify_student_photoverification', 'user_id'),
    ('edly_edlyuserprofile', 'user_id'),
    ('student_courseaccessrole', 'user_id'),

    # S1: trivial user/student-id-keyed adds, verified against this
    # checkout's actual installed apps.
    ('courseware_xmodulestudentinfofield', 'student_id'),
    ('courseware_xmodulestudentprefsfield', 'student_id'),
    ('edly_studentcourseprogress', 'student_id'),
    ('courseware_studentfieldoverride', 'student_id'),
    ('student_userattribute', 'user_id'),
    ('user_api_userpreference', 'user_id'),
    ('user_api_usercoursetag', 'user_id'),
    ('external_user_ids_externalid', 'user_id'),
    ('edly_twofactorbypass', 'user_id'),
    ('bookmarks_bookmark', 'user_id'),
    ('course_goals_coursegoal', 'user_id'),
    ('milestones_usermilestone', 'user_id'),
    ('edx_when_userdate', 'user_id'),
    ('teams_courseteammembership', 'user_id'),
    # Django's auto-generated M2M through-table for
    # django_comment_common.Role.users (Role's own db_table is overridden
    # to 'django_comment_client_role'; Django names the through-table off
    # that overridden db_table, giving this exact name).
    ('django_comment_client_role_users', 'user_id'),

    # S2: figures analytics (edly-io/figures fork is installed here).
    ('figures_enrollmentdata', 'user_id'),
    ('figures_learnercoursegrademetrics', 'user_id'),
]

# No direct user/course column -- scoped via student_courseenrollment.id
# instead (their only course linkage is the enrollment FK, not a course
# column of their own).
ENROLLMENT_LINKED_TABLES = ['student_courseenrollmentattribute', 'student_manualenrollmentaudit']

# S3: CHAR columns that store a stringified user id rather than an integer
# FK -- ids must be cast to str before binding, or an int IN (...) against
# a varchar column matches nothing. kwl-xblock's own column is literally
# named `user` (reserved word -- `bt()` handles the quoting).
STRING_CAST_TABLES = [
    ('kwl_djangoapp_kwlmodel', 'user'),
    ('lti_consumer_ltiagsscore', 'user_id'),
]

# B3 correction: verify_student_softwaresecurephotoverification is a Django
# multi-table-inheritance child of PhotoVerification (mapped to
# verify_student_photoverification, already in TABLE_SCOPE above). Fetch the
# child by the parent's captured ids via its ptr FK.
VERIFY_STUDENT_PARENT_TABLE = 'verify_student_photoverification'
VERIFY_STUDENT_CHILD_TABLE = 'verify_student_softwaresecurephotoverification'
VERIFY_STUDENT_CHILD_FK = 'photoverification_ptr_id'

# S6: certificates_certificateinvalidation has no user column of its own --
# scoped via the generated_certificate_id FK to the already-scoped
# certificates_generatedcertificate table (ids captured while streaming it).
CERTIFICATE_INVALIDATION_TABLE = 'certificates_certificateinvalidation'
CERTIFICATE_INVALIDATION_FK = 'generated_certificate_id'

# S5: grades_visibleblocks is keyed by course_id/hashed only, no user column
# at all. Rather than deriving a course set from org-filtered courses (which
# would reintroduce the same org-filtering drop this file's scoping decision
# removes -- see module docstring), this captures the visible_blocks_hash
# FK value directly while streaming the already-user-scoped
# grades_persistentsubsectiongrade table, and fetches exactly those blocks.
VISIBLE_BLOCKS_TABLE = 'grades_visibleblocks'
VISIBLE_BLOCKS_COLUMN = 'hashed'
VISIBLE_BLOCKS_FK_COLUMN = 'visible_blocks_hash'  # column on grades_persistentsubsectiongrade

# B1: the full ORA/submissions FK chain, in fetch order (see
# `_export_ora_chain` and the module docstring for the chain itself and
# what's deliberately excluded). Listed here for ALL_TABLES/documentation
# purposes; the actual fetch/capture logic lives in `_export_ora_chain`.
ORA_CHAIN_TABLES = [
    'submissions_studentitem',
    'submissions_submission',
    'submissions_score',
    'submissions_scoresummary',
    'submissions_scoreannotation',
    'submissions_teamsubmission',
    'assessment_assessment',
    'assessment_assessmentpart',
    'assessment_assessmentfeedback',
    'assessment_peerworkflow',
    'assessment_peerworkflowitem',
    'assessment_staffworkflow',
    'assessment_teamstaffworkflow',
    'assessment_studenttrainingworkflow',
    'assessment_studenttrainingworkflowitem',
    'assessment_sharedfileupload',
]

ALL_TABLES = (
    [table for table, _ in TABLE_SCOPE]
    + ENROLLMENT_LINKED_TABLES
    + [table for table, _ in STRING_CAST_TABLES]
    + [VERIFY_STUDENT_CHILD_TABLE, CERTIFICATE_INVALIDATION_TABLE, VISIBLE_BLOCKS_TABLE]
    + ORA_CHAIN_TABLES
)

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


def _hex_uuid_to_canonical(value):
    """
    Convert a raw 32-char hex UUID (no hyphens) into the canonical 36-char
    hyphenated string -- see the module docstring's "submission_uuid format
    mismatch" note. Used only to convert the in-memory `submission_uuids`
    matching list built in `_export_ora_chain`; the row data written to
    `submissions_submission.json` must stay in the raw form exactly as read
    from the DB, so this is never applied to anything but that one sink.
    """
    return str(uuid.UUID(hex=value)) if value else value


class Command(BaseCommand):
    """
    Export one Edly tenant's learner data (accounts, profiles, tenant
    membership, enrollment status, grades, certificates, course progress,
    ORA/assessment history, and more -- see TABLE_SCOPE/ORA_CHAIN_TABLES) to
    a portable JSON bundle.
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
            help='Bundle directory to write into (default: EDM_EXPORT_DIR or '
                 '<MEDIA_ROOT>/edm_exports/<slug>_<timestamp>/).',
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
        Resolve the tenant's members and export the learner tables (see
        module docstring: scoping is membership-only, course_org_filter is
        informational only and never gates or filters the export).
        """
        slug = options['slug']
        dry_run = options['dry_run']
        self.batch_size = options['batch_size']

        self._print_header(slug, dry_run)

        sub_org = self._get_sub_org(slug)
        orgs = self._resolve_orgs(sub_org)
        course_ids = self._get_course_ids(orgs)
        user_ids = self._get_user_ids(sub_org)

        self.stdout.write(u"Orgs: {0}".format(orgs))
        self.stdout.write(
            u"Courses resolved (informational only -- no longer used to scope exported "
            u"data; see module docstring): {0}".format(len(course_ids))
        )
        self.stdout.write(u"Users resolved: {0}".format(len(user_ids)))

        if not user_ids:
            self.stdout.write(self.style.WARNING("No users found for this tenant -- nothing to export."))
            return

        if dry_run:
            self._dry_run(user_ids)
            return

        timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
        bundle_dir = self._resolve_bundle_dir(slug, options.get('output_dir'), timestamp)
        self._make_private_dir(bundle_dir)
        learners_dir = os.path.join(bundle_dir, 'learners')
        self._make_private_dir(learners_dir)

        counts = {}
        checksums = {}
        enrollment_ids = []
        anon_ids = []
        cert_ids = []
        visible_blocks_hashes = []
        photo_verification_ids = []

        for table, user_col in TABLE_SCOPE:
            columns, rows = self._fetch_by_ids(table, user_col, user_ids)
            if table == USERPROFILE_TABLE:
                columns, rows = self._strip_columns(columns, rows, USERPROFILE_DROPPED_COLUMNS)
            if table == 'student_courseenrollment' and 'id' in columns:
                rows = self._capture_column(rows, columns.index('id'), enrollment_ids)
            if table == 'student_anonymoususerid' and 'anonymous_user_id' in columns:
                rows = self._capture_column(rows, columns.index('anonymous_user_id'), anon_ids)
            if table == 'certificates_generatedcertificate' and 'id' in columns:
                rows = self._capture_column(rows, columns.index('id'), cert_ids)
            if table == 'grades_persistentsubsectiongrade' and VISIBLE_BLOCKS_FK_COLUMN in columns:
                rows = self._capture_column(rows, columns.index(VISIBLE_BLOCKS_FK_COLUMN), visible_blocks_hashes)
            if table == VERIFY_STUDENT_PARENT_TABLE and 'id' in columns:
                rows = self._capture_column(rows, columns.index('id'), photo_verification_ids)
            self._write_and_record(learners_dir, counts, checksums, table, columns, rows)

        for table in ENROLLMENT_LINKED_TABLES:
            columns, rows = self._fetch_by_ids(table, 'enrollment_id', enrollment_ids)
            self._write_and_record(learners_dir, counts, checksums, table, columns, rows)

        # S3: CHAR-column user tables -- cast ids to str before binding.
        str_user_ids = [text_type(user_id) for user_id in user_ids]
        for table, col in STRING_CAST_TABLES:
            columns, rows = self._fetch_by_ids(table, col, str_user_ids)
            self._write_and_record(learners_dir, counts, checksums, table, columns, rows)

        # B3 correction: verify_student_softwaresecurephotoverification (a
        # multi-table-inheritance child of VERIFY_STUDENT_PARENT_TABLE) is
        # chained off photo_verification_ids, captured above while the
        # TABLE_SCOPE loop fetched+wrote the parent table.
        columns, rows = self._fetch_by_ids(VERIFY_STUDENT_CHILD_TABLE, VERIFY_STUDENT_CHILD_FK, photo_verification_ids)
        self._write_and_record(learners_dir, counts, checksums, VERIFY_STUDENT_CHILD_TABLE, columns, rows)

        # S6: certificate invalidation, chained off captured certificate ids.
        columns, rows = self._fetch_by_ids(CERTIFICATE_INVALIDATION_TABLE, CERTIFICATE_INVALIDATION_FK, cert_ids)
        self._write_and_record(learners_dir, counts, checksums, CERTIFICATE_INVALIDATION_TABLE, columns, rows)

        # S5: visible blocks, chained off captured visible_blocks_hash values.
        columns, rows = self._fetch_by_ids(VISIBLE_BLOCKS_TABLE, VISIBLE_BLOCKS_COLUMN, visible_blocks_hashes)
        self._write_and_record(learners_dir, counts, checksums, VISIBLE_BLOCKS_TABLE, columns, rows)

        # B1: full ORA/submissions chain, seeded from the anonymous ids
        # captured above.
        self._export_ora_chain(learners_dir, anon_ids, counts, checksums)

        columns, rows = self._fetch_membership_table(user_ids)
        self._write_and_record(learners_dir, counts, checksums, MEMBERSHIP_TARGET_NAME, columns, rows)

        self._write_manifest(bundle_dir, slug, counts, checksums)

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
        Resolve the tenant's course_org_filter, for informational/dry-run
        display only -- see module docstring: this no longer gates or
        filters the export.

        Primary: SiteConfiguration.get_value('course_org_filter') off the
        tenant's lms_site. Fallback/cross-check: the sub_org's own
        edx_organizations M2M, for tenants whose site configuration doesn't
        set course_org_filter.
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
        Resolve course ids (as strings) for the tenant's orgs via
        CourseOverview -- informational/dry-run display only (see
        `_resolve_orgs`); no table is filtered by this list.
        """
        return [
            text_type(course_id)
            for course_id in CourseOverview.objects.filter(org__in=orgs).values_list('id', flat=True)
        ]

    def _get_user_ids(self, sub_org):
        """
        Resolve the tenant's user ids strictly via EdlyMultiSiteAccess
        membership -- the sole tenant boundary this command recognizes (see
        module docstring). There is deliberately no enrollment-derived
        fallback: a user enrolled in one of the tenant's courses without an
        EdlyMultiSiteAccess row is not a member and must not be included.
        """
        return set(EdlyMultiSiteAccess.objects.filter(sub_org=sub_org).values_list('user_id', flat=True))

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

    # ------------------------------------------------------------------ table fetch

    def _fetch_by_ids(self, table, column, ids):
        """
        Fetch a table's rows matching a single column IN ids -- the common
        case of `_fetch_by_any_id` (below) where a row is reachable via just
        one FK/id chain (e.g. the TABLE_SCOPE user/student-id tables by
        user_ids, or student_courseenrollmentattribute by enrollment_id).

        Returns (columns, rows) where `rows` is a lazy generator (see
        `_paginate`) -- callers should stream it (e.g. via
        `_write_table_json`) rather than materializing it with list()/len().
        """
        return self._fetch_by_any_id(table, [(column, ids)])

    def _fetch_by_any_id(self, table, column_ids_pairs):
        """
        Fetch a table's rows matching col_1 IN ids_1 OR col_2 IN ids_2 OR ...
        -- used where a row can be reached via more than one FK/id chain at
        once, e.g. an ORA assessment row for a submission a member made, OR
        a row scored BY that member acting as a peer/staff grader (see B1
        in the module docstring). A pair whose `ids` is empty is skipped
        entirely (an empty `col IN ()` isn't valid SQL to begin with, and
        MySQL treats it as always-false anyway). If every pair is empty,
        nothing is fetched -- same short-circuit as the old
        needs_course_filter-with-no-courses case.
        """
        columns = self._columns(table)
        clauses = []
        args = []
        for column, ids in column_ids_pairs:
            if ids:
                clauses.append(u"{0} IN ({1})".format(bt(column), ', '.join(['%s'] * len(ids))))
                args += list(ids)
        if not clauses:
            return columns, iter(())
        where_sql = u" OR ".join(clauses)
        return columns, self._paginate(table, columns, where_sql, args)

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

    def _export_ora_chain(self, learners_dir, anon_ids, counts, checksums):
        """
        Fetch + write the ORA/submissions FK chain (B1): open-response
        assessments and their scoring aren't keyed by auth_user.id at all --
        they're reached via the anonymized ids captured from
        student_anonymoususerid while streaming TABLE_SCOPE, then a chain of
        FKs through the `submissions` and `assessment` apps' own tables
        (verified against the real edx-submissions/edx-ora2 models -- see
        module docstring for exactly which reference tables, e.g. rubrics,
        are deliberately excluded).

        Each stage below fully drains (writes) the previous stage's rows
        before using its captured ids -- same requirement as every other
        `_capture_column` use in this file: a sink isn't populated until the
        generator it wraps has been iterated to completion, which
        `_write_and_record` does via `_write_table_json`.
        """
        # -- submissions app ---------------------------------------------
        student_item_ids = []
        columns, rows = self._fetch_by_ids('submissions_studentitem', 'student_id', anon_ids)
        if 'id' in columns:
            rows = self._capture_column(rows, columns.index('id'), student_item_ids)
        self._write_and_record(learners_dir, counts, checksums, 'submissions_studentitem', columns, rows)

        submission_ids, submission_uuids, team_submission_ids = [], [], []
        columns, rows = self._fetch_by_ids('submissions_submission', 'student_item_id', student_item_ids)
        if 'id' in columns:
            rows = self._capture_column(rows, columns.index('id'), submission_ids)
        if 'uuid' in columns:
            # submission_uuids is used below to match against assessment_*
            # tables' plain-CharField submission_uuid columns, which need
            # the canonical hyphenated form -- see _hex_uuid_to_canonical
            # and the module docstring.
            rows = self._capture_column(
                rows, columns.index('uuid'), submission_uuids, transform=_hex_uuid_to_canonical
            )
        if 'team_submission_id' in columns:
            rows = self._capture_column(rows, columns.index('team_submission_id'), team_submission_ids)
        self._write_and_record(learners_dir, counts, checksums, 'submissions_submission', columns, rows)
        # team_submission_id is nullable -- most rows aren't team
        # submissions, so drop the Nones before using this as an IN list.
        team_submission_ids = [tsid for tsid in team_submission_ids if tsid is not None]

        score_ids = []
        columns, rows = self._fetch_by_ids('submissions_score', 'student_item_id', student_item_ids)
        if 'id' in columns:
            rows = self._capture_column(rows, columns.index('id'), score_ids)
        self._write_and_record(learners_dir, counts, checksums, 'submissions_score', columns, rows)

        columns, rows = self._fetch_by_ids('submissions_scoresummary', 'student_item_id', student_item_ids)
        self._write_and_record(learners_dir, counts, checksums, 'submissions_scoresummary', columns, rows)

        columns, rows = self._fetch_by_ids('submissions_scoreannotation', 'score_id', score_ids)
        self._write_and_record(learners_dir, counts, checksums, 'submissions_scoreannotation', columns, rows)

        columns, rows = self._fetch_by_ids('submissions_teamsubmission', 'id', team_submission_ids)
        self._write_and_record(learners_dir, counts, checksums, 'submissions_teamsubmission', columns, rows)

        # -- assessment (ORA2/openassessment) app --------------------------
        assessment_ids = []
        columns, rows = self._fetch_by_any_id(
            'assessment_assessment', [('submission_uuid', submission_uuids), ('scorer_id', anon_ids)]
        )
        if 'id' in columns:
            rows = self._capture_column(rows, columns.index('id'), assessment_ids)
        self._write_and_record(learners_dir, counts, checksums, 'assessment_assessment', columns, rows)

        columns, rows = self._fetch_by_ids('assessment_assessmentpart', 'assessment_id', assessment_ids)
        self._write_and_record(learners_dir, counts, checksums, 'assessment_assessmentpart', columns, rows)

        columns, rows = self._fetch_by_ids('assessment_assessmentfeedback', 'submission_uuid', submission_uuids)
        self._write_and_record(learners_dir, counts, checksums, 'assessment_assessmentfeedback', columns, rows)

        peerworkflow_ids = []
        columns, rows = self._fetch_by_any_id(
            'assessment_peerworkflow', [('submission_uuid', submission_uuids), ('student_id', anon_ids)]
        )
        if 'id' in columns:
            rows = self._capture_column(rows, columns.index('id'), peerworkflow_ids)
        self._write_and_record(learners_dir, counts, checksums, 'assessment_peerworkflow', columns, rows)

        # PeerWorkflowItem.scorer_id/author_id are FKs to PeerWorkflow.id --
        # despite the column name, these are NOT anonymized user ids, so
        # this filters on peerworkflow_ids, not anon_ids.
        columns, rows = self._fetch_by_any_id(
            'assessment_peerworkflowitem', [('scorer_id', peerworkflow_ids), ('author_id', peerworkflow_ids)]
        )
        self._write_and_record(learners_dir, counts, checksums, 'assessment_peerworkflowitem', columns, rows)

        staffworkflow_ids = []
        columns, rows = self._fetch_by_any_id(
            'assessment_staffworkflow', [('submission_uuid', submission_uuids), ('scorer_id', anon_ids)]
        )
        if 'id' in columns:
            rows = self._capture_column(rows, columns.index('id'), staffworkflow_ids)
        self._write_and_record(learners_dir, counts, checksums, 'assessment_staffworkflow', columns, rows)

        # TeamStaffWorkflow is a multi-table-inheritance child of
        # StaffWorkflow (same shape as verify_student's PhotoVerification/
        # SoftwareSecurePhotoVerification split -- see module docstring):
        # its own table only carries team_submission_uuid + the ptr FK.
        columns, rows = self._fetch_by_ids(
            'assessment_teamstaffworkflow', 'staffworkflow_ptr_id', staffworkflow_ids
        )
        self._write_and_record(learners_dir, counts, checksums, 'assessment_teamstaffworkflow', columns, rows)

        studenttrainingworkflow_ids = []
        columns, rows = self._fetch_by_any_id(
            'assessment_studenttrainingworkflow', [('submission_uuid', submission_uuids), ('student_id', anon_ids)]
        )
        if 'id' in columns:
            rows = self._capture_column(rows, columns.index('id'), studenttrainingworkflow_ids)
        self._write_and_record(learners_dir, counts, checksums, 'assessment_studenttrainingworkflow', columns, rows)

        columns, rows = self._fetch_by_ids(
            'assessment_studenttrainingworkflowitem', 'workflow_id', studenttrainingworkflow_ids
        )
        self._write_and_record(
            learners_dir, counts, checksums, 'assessment_studenttrainingworkflowitem', columns, rows
        )

        # Cheap bonus given anon_ids is already in hand -- team file
        # uploads, same owner_id shape as everything else here.
        columns, rows = self._fetch_by_ids('assessment_sharedfileupload', 'owner_id', anon_ids)
        self._write_and_record(learners_dir, counts, checksums, 'assessment_sharedfileupload', columns, rows)

    def _paginate(self, table, columns, where_sql, where_args):
        """
        Yield a table's matching rows one at a time, fetching `self.batch_size`
        rows per SELECT instead of a single `fetchall()` -- tables like
        `courseware_studentmodule` can be millions of rows with KB-sized
        `state` blobs, a real OOM risk for a one-shot fetch.

        Keyset-paginates by `id` (`id > last_id`) when the table has one,
        falling back to OFFSET otherwise -- this also transparently covers
        Django multi-table-inheritance child tables (e.g.
        `verify_student_softwaresecurephotoverification`,
        `assessment_teamstaffworkflow`) whose primary key is a `*_ptr_id`
        column rather than a plain `id`. OFFSET pagination is non-atomic
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

    def _capture_column(self, rows, col_idx, sink, transform=None):
        """
        Pass rows through unchanged while recording one column's values into
        `sink` as they stream past -- used throughout this file (enrollment
        ids, anonymous user ids, certificate ids, visible-blocks hashes, ORA
        chain ids, ...) to grab an id/FK column for a later query without
        materializing the whole table just to read that one column.

        `transform`, if given, is applied to the value only as it's
        appended to `sink` -- the row yielded onward (and therefore what
        ends up written to the bundle file) is always the raw, untouched
        value. Used for `submissions_submission.uuid` (see
        `_hex_uuid_to_canonical`): the in-memory matching list needs the
        canonical hyphenated form, but the bundle must keep the raw hex
        form that a same-schema restore would need to write back verbatim.
        """
        for row in rows:
            value = row[col_idx]
            sink.append(transform(value) if transform is not None else value)
            yield row

    # ------------------------------------------------------------------ bundle output

    def _write_and_record(self, learners_dir, counts, checksums, table, columns, rows):
        """
        Write one table's JSON into the bundle and record its row count +
        checksum -- the common tail of every fetch-then-write step in
        handle() and `_export_ora_chain`.
        """
        row_count, digest = self._write_table_json(
            os.path.join(learners_dir, u"{0}.json".format(table)), columns, rows
        )
        counts[table] = row_count
        checksums[table] = digest
        self.stdout.write(u"  {0}: {1} rows".format(table, row_count))

    def _resolve_bundle_dir(self, slug, output_dir, timestamp):
        """
        `--output-dir` wins verbatim if given; otherwise EDM_EXPORT_DIR/<slug>_<timestamp>/,
        falling back to a directory under MEDIA_ROOT (rather than bare /tmp,
        which a tmp-reaper can purge before an operator retrieves the
        bundle) when neither override is set.
        """
        if output_dir:
            return output_dir
        base = getattr(settings, 'EDM_EXPORT_DIR', os.path.join(settings.MEDIA_ROOT, 'edm_exports'))
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
        would defeat the point of batching the SELECTs. Returns
        (row_count, sha256_hexdigest) -- the digest (N2) is computed
        incrementally over the exact bytes written, alongside the write
        itself, rather than re-reading the file afterward.
        """
        row_count = 0
        digest = hashlib.sha256()

        def _emit(output_file, chunk):
            output_file.write(chunk)
            digest.update(chunk.encode('utf-8'))

        with self._open_bundle_file(path) as output_file:
            _emit(output_file, u'{"columns": ')
            _emit(output_file, json.dumps(columns))
            _emit(output_file, u', "rows": [')
            for row in rows:
                if row_count:
                    _emit(output_file, u', ')
                _emit(output_file, json.dumps(row, default=self._json_default))
                row_count += 1
            _emit(output_file, u']}')
        return row_count, digest.hexdigest()

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

    def _write_manifest(self, bundle_dir, slug, counts, checksums):
        """
        Write MANIFEST.json in the exact shape the Ulmo-side import_learner_data command reads.

        `checksums` (N2) is added as a sibling top-level key to `components`
        rather than folded into it -- the importer reads
        `components.learners[table]` as a plain row-count int today, and
        changing those entries to `{rows, sha256}` dicts would break that
        read. Adding a separate `checksums.learners[table] = sha256hex` key
        is purely additive: existing readers are unaffected, and an
        import-side integrity check can be added later by reading this new
        key without any format migration.
        """
        manifest = {
            'slug': slug,
            'release_line': self._release_line(),
            'schema_state': self._schema_state(),
            'components': {'learners': counts},
            'checksums': {'learners': checksums},
        }
        manifest_path = os.path.join(bundle_dir, MANIFEST_FILENAME)
        with self._open_bundle_file(manifest_path) as manifest_file:
            manifest_file.write(json.dumps(manifest, indent=2, default=self._json_default))

    # ------------------------------------------------------------------ dry run / reporting

    def _dry_run(self, user_ids):
        """
        Print per-table row counts without writing any files. Tables whose
        scope depends on ids captured during a real run (enrollment-linked,
        the ORA chain, verify_student's child table, certificate
        invalidation, visible blocks) can't be counted here -- they print a
        placeholder line instead, same as the enrollment-linked tables did
        before this change.
        """
        for table, user_col in TABLE_SCOPE:
            where_sql = u"{0} IN ({1})".format(bt(user_col), ', '.join(['%s'] * len(user_ids)))
            with connection.cursor() as cursor:
                cursor.execute(u"SELECT COUNT(*) FROM {0} WHERE {1}".format(bt(table), where_sql), list(user_ids))
                count = cursor.fetchone()[0]
            self.stdout.write(u"  {0}: {1} rows".format(table, count))

        deferred_tables = (
            ENROLLMENT_LINKED_TABLES
            + [VERIFY_STUDENT_CHILD_TABLE, CERTIFICATE_INVALIDATION_TABLE, VISIBLE_BLOCKS_TABLE]
            + ORA_CHAIN_TABLES
        )
        for table in deferred_tables:
            self.stdout.write(
                u"  {0}: depends on captured ids from a real run (resolved then)".format(table)
            )

        str_user_ids = [text_type(user_id) for user_id in user_ids]
        for table, col in STRING_CAST_TABLES:
            where_sql = u"{0} IN ({1})".format(bt(col), ', '.join(['%s'] * len(str_user_ids)))
            with connection.cursor() as cursor:
                cursor.execute(u"SELECT COUNT(*) FROM {0} WHERE {1}".format(bt(table), where_sql), str_user_ids)
                count = cursor.fetchone()[0]
            self.stdout.write(u"  {0}: {1} rows".format(table, count))

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
