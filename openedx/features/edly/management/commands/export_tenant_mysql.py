"""
Dump tier 2/3/4/5/6/7/8/9/10 (+ extras) `edxapp` tables for one Edly tenant, tenant-scoped via
live SQL subqueries (never a Python-collected user-id list -- see
`tenant_export/sqlutil.py`). Part of the MIT off-boarding export tooling
(EDLYPRODUCT-8584 Phase 1); the scoping/security logic itself (every WHERE
clause, the tier-8 ORA UUID chain, the two audited leak fixes, the secrets
list) is ported unchanged from the independently-audited standalone
reference implementation -- see `tenant_export/tables.py`,
`tenant_export/ora_chain.py`, and `tenant_export/secrets.py` for the logic
and its citations.

Reads `scope.json` (written by `export_tenant_scope`) -- never re-resolves
scope independently. Every subcommand in this family shares one
`MANIFEST.json` under `--out-dir`; `status` only reads "complete" once every
table in `tenant_export.tables.EXPECTED_TABLES` (which includes the separate
`export_tenant_csmh` table too) has reached a terminal status.

Idempotent and resumable: checks `<out-dir>/.state/<slug>/<table>.done`
before dumping, skips completed tables, writes `<table>.sql.partial` ->
`os.replace()` -> `<table>.sql` + the `.done` marker, so a crash mid-dump
never leaves a half-written file looking complete.

Usage:
    python manage.py lms export_tenant_mysql MIT --scope scope.json --out-dir ./out
    python manage.py lms export_tenant_mysql MIT --scope scope.json --out-dir ./out --dry-run
    python manage.py lms export_tenant_mysql MIT --scope scope.json --out-dir ./out --tables auth_user,auth_userprofile
"""
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from openedx.features.edly.tenant_export import dbutil, manifest as manifest_mod, ora_chain, resume, tables
from openedx.features.edly.tenant_export import secrets as secrets_mod
from openedx.features.edly.tenant_export.scope import load_scope_file
from openedx.features.edly.tenant_export.sqlutil import count_rows


def _total_count(cursor, table, where_clauses):
    return sum(count_rows(cursor, table, c) for c in where_clauses) if where_clauses else 0


def _where_clauses_for(table, sub_org_id, course_org_filter, tier8_ids):
    if table in tables.TIER_3:
        return [tables.tier3_where(table, sub_org_id, course_org_filter)]
    if table in tables.TIER_5:
        return [tables.tier5_where(table, sub_org_id)]
    if table in tables.TIER_6:
        return [tables.tier6_where(table, course_org_filter)]
    if table in tables.TIER_7:
        return [tables.tier7_where(table, sub_org_id, course_org_filter)]
    if table in tables.TIER_8:
        return ora_chain.get_tier8_where_clauses(table, tier8_ids, sub_org_id, course_org_filter)
    if table in tables.OTHER_TIER_TABLES:  # tiers 2/4/9/10 + non-tier extras
        return [tables.tier_other_where(table, sub_org_id, course_org_filter)]
    raise AssertionError(f"table {table!r} is not in any known tier")


class Command(BaseCommand):
    help = (
        "Dump tier 2-10 edxapp tables for one Edly tenant into --out-dir, "
        "tenant-scoped via scope.json (see export_tenant_scope)."
    )

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument('--scope', required=True, help='Path to scope.json (see export_tenant_scope).')
        parser.add_argument('--out-dir', required=True, help='Directory to write dump files + MANIFEST.json into.')
        parser.add_argument(
            '--tables',
            help='Comma-separated subset; must be a subset of tables.ALL_TIER_TABLES, never a DENYLIST table.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='SELECT COUNT(*) per table instead of dumping; writes no .sql files.',
        )

    def handle(self, *args, **options):
        os.umask(0o077)  # every file this command writes is learner PII

        scope_data = load_scope_file(options['scope'])
        if options['slug'] != scope_data['slug']:
            raise CommandError(
                f"slug {options['slug']!r} does not match scope.json's slug {scope_data['slug']!r}"
            )

        sub_org_id = scope_data['sub_org_id']
        course_org_filter = scope_data['course_org_filter']

        requested = (
            [t.strip() for t in options['tables'].split(',') if t.strip()]
            if options['tables'] else list(tables.ALL_TIER_TABLES)
        )

        try:
            secrets_mod.check_denylist(requested)
        except secrets_mod.DenylistedTableError as exc:
            raise CommandError(str(exc))

        unknown = set(requested) - set(tables.ALL_TIER_TABLES)
        if unknown:
            raise CommandError(f"unknown/out-of-scope tables requested (not in tables.ALL_TIER_TABLES): {sorted(unknown)}")

        out_dir = Path(options['out_dir'])
        out_dir.mkdir(parents=True, exist_ok=True)
        conn_params = dbutil.conn_params_from_settings_dict(connection.settings_dict)

        scope_sha = manifest_mod.sha256_of_text(Path(options['scope']).read_text())
        mf = manifest_mod.Manifest(
            out_dir / "MANIFEST.json", scope_data['slug'], scope_sha, tables.EXPECTED_TABLES,
            expected_excluded=tables.excluded_keys(),
        )
        manifest_mod.seed_excluded_entries(mf)

        tier8_ids = None

        with connection.cursor() as cursor:
            for table in requested:
                if resume.is_done(out_dir, scope_data['slug'], table):
                    self.stdout.write(f"skip (done): {table}")
                    continue
                if not dbutil.table_exists(cursor, table):
                    mf.update_table(table, status="skipped_not_in_source")
                    self.stdout.write(f"skip (not in source): {table}")
                    continue

                if table in tables.TIER_8 and tier8_ids is None:
                    self.stdout.write("prefetching tier-8 ORA/assessment id chain...")
                    tier8_ids = ora_chain.prefetch_tier8_ids(cursor, sub_org_id, course_org_filter)
                    self.stdout.write("running UUID-trap guard (tier-8 chain vs. independent direct counts)...")
                    ora_chain.run_uuid_trap_guard(cursor, course_org_filter, tier8_ids)
                    self.stdout.write("UUID-trap guard passed.")

                where_clauses = _where_clauses_for(table, sub_org_id, course_org_filter, tier8_ids)

                if options['dry_run']:
                    count = _total_count(cursor, table, where_clauses)
                    self.stdout.write(f"[dry-run] {table}: {count} rows")
                    continue

                out_path = out_dir / f"{table}.sql"
                partial_path = out_dir / f"{table}.sql.partial"
                if partial_path.exists():
                    partial_path.unlink()

                try:
                    if table in secrets_mod.SECRET_COLUMNS:
                        rows = dbutil.dump_redacted_table(conn_params, table, where_clauses[0], partial_path, cursor)
                        redacted_cols = sorted(secrets_mod.SECRET_COLUMNS[table])
                    else:
                        dbutil.dump_table_chunked(conn_params, table, partial_path, where_clauses)
                        rows = _total_count(cursor, table, where_clauses)
                        redacted_cols = []

                    os.replace(partial_path, out_path)
                    sha = dbutil.sha256_file(out_path)
                    mf.update_table(table, status="complete", rows=rows, sha256=sha, redacted_columns=redacted_cols)
                    resume.mark_done(out_dir, scope_data['slug'], table)
                    self.stdout.write(f"dumped {table}: {rows} rows")
                except Exception as exc:  # pylint: disable=broad-except -- one bad table must not abort the whole run
                    err = dbutil.describe_error(exc)
                    mf.update_table(table, status="error", error=err)
                    self.stderr.write(self.style.ERROR(f"ERROR dumping {table}: {err}"))

        status = mf.finalize()
        self.stdout.write(f"manifest status: {status}")
