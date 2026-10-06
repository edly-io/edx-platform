"""
Dump the CSMH (courseware student-module history) table for one Edly
tenant. Part of the MIT off-boarding export tooling (EDLYPRODUCT-8584
Phase 1).

CSMH lives in a genuinely separate database (`edxapp_csmh`, routed via
Django's `student_module_history` DB alias --
`openedx.core.lib.django_courseware_routers.StudentModuleHistoryExtendedRouter`,
confirmed in `lms/envs/common.py`), linked to the main `edxapp` database's
`courseware_studentmodule` only by `student_module_id` -- a non-DB-
constrained FK (`db_constraint=False`,
`coursewarehistoryextended/models.py`) crossing databases, which
`mysqldump` cannot express as a cross-database subquery. See
`tenant_export/csmh.py` for the two-step id-resolve-then-chunked-dump logic.

Shares the same MANIFEST.json as `export_tenant_mysql` (see that command's
docstring) -- run them against the same `--out-dir`.

Usage:
    python manage.py lms export_tenant_csmh MIT --scope scope.json --out-dir ./out
    python manage.py lms export_tenant_csmh MIT --scope scope.json --out-dir ./out --dry-run
"""
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, connections
from django.db.utils import ConnectionDoesNotExist

from openedx.features.edly.tenant_export import csmh, dbutil, manifest as manifest_mod, resume, tables
from openedx.features.edly.tenant_export.scope import load_scope_file


class Command(BaseCommand):
    help = "Dump the CSMH (courseware student module history) table for one Edly tenant."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument('--scope', required=True, help='Path to scope.json (see export_tenant_scope).')
        parser.add_argument(
            '--out-dir', required=True, help='Directory to write into (same one export_tenant_mysql uses).',
        )
        parser.add_argument(
            '--dry-run', action='store_true', help='Count rows instead of dumping; writes no .sql file.',
        )

    def handle(self, *args, **options):
        os.umask(0o077)  # every file this command writes is learner PII

        scope_data = load_scope_file(options['scope'])
        if options['slug'] != scope_data['slug']:
            raise CommandError(
                f"slug {options['slug']!r} does not match scope.json's slug {scope_data['slug']!r}"
            )

        try:
            csmh_connection = connections['student_module_history']
        except ConnectionDoesNotExist:
            raise CommandError(
                "no 'student_module_history' DB alias is configured in this environment's settings -- "
                "see lms/envs/common.py's DATABASES for the devstack default; a deployed environment's "
                "AUTH_TOKENS-driven DATABASES config (lms/envs/production.py) must define this alias too, "
                "or CSMH simply isn't reachable from this box."
            )
        csmh_conn_params = dbutil.conn_params_from_settings_dict(csmh_connection.settings_dict)

        out_dir = Path(options['out_dir'])
        out_dir.mkdir(parents=True, exist_ok=True)

        scope_sha = manifest_mod.sha256_of_text(Path(options['scope']).read_text())
        mf = manifest_mod.Manifest(out_dir / "MANIFEST.json", scope_data['slug'], scope_sha, tables.EXPECTED_TABLES)
        manifest_mod.seed_excluded_entries(mf)

        table = tables.CSMH_TABLE
        if resume.is_done(out_dir, scope_data['slug'], table):
            self.stdout.write(f"skip (done): {table}")
            return

        with connection.cursor() as cursor:
            if options['dry_run']:
                count = csmh.dump_csmh(cursor, csmh_conn_params, scope_data['course_org_filter'], None, dry_run=True)
                self.stdout.write(f"[dry-run] {table}: {count} rows")
                return

            out_path = out_dir / f"{table}.sql"
            partial_path = out_dir / f"{table}.sql.partial"
            if partial_path.exists():
                partial_path.unlink()
            try:
                rows = csmh.dump_csmh(
                    cursor, csmh_conn_params, scope_data['course_org_filter'], partial_path, dry_run=False,
                )
                os.replace(partial_path, out_path)
                sha = dbutil.sha256_file(out_path)
                mf.update_table(table, status="complete", rows=rows, sha256=sha, redacted_columns=[])
                resume.mark_done(out_dir, scope_data['slug'], table)
                self.stdout.write(f"dumped {table}: {rows} rows")
            except Exception as exc:  # pylint: disable=broad-except
                err = dbutil.describe_error(exc)
                mf.update_table(table, status="error", error=err)
                self.stderr.write(self.style.ERROR(f"ERROR dumping {table}: {err}"))

        status = mf.finalize()
        self.stdout.write(f"manifest status: {status}")
