"""
Dump one Edly tenant's rows from a service database -- credentials,
discovery or ecommerce -- into --out-dir. Part of the MIT off-boarding
export tooling (EDLYPRODUCT-8584 Phase 2); same scope.json / MANIFEST.json /
resume / secret-redaction machinery as `export_tenant_mysql`, but the
tables, WHERE builders and redactions come from the per-service specs in
`tenant_export/svc_credentials.py`, `svc_discovery.py`, `svc_ecommerce.py`.

Needs a `services.<db>` block in scope.json (run `export_tenant_scope
--services ...` first).

Output is the flat layout: `<out-dir>/<db>__<table>.sql`, state marker
`.state/<slug>/<db>__<table>.done`, and a manifest entry keyed by the same
stem carrying `db`. Policy: customer/order PII is kept, secrets only are
redacted (whole blob columns blanked -- see the spec modules); abandoned
ecommerce baskets are excluded.

DB CONNECTION: the LMS has no Django alias for these databases. This command
registers one at runtime from the LMS `default` connection's settings (same
host/user/password/port, database name = the service name), optionally
overridden per service by the Django setting `EXPORT_TENANT_DATABASES`, e.g.
in lms/envs/private.py:

    EXPORT_TENANT_DATABASES = {'ecommerce': {'HOST': 'ecommerce-db', 'USER': 'ro', 'PASSWORD': '...'}}

Nothing in lms/envs defaults changes. See `tenant_export/services.py`.

Usage:
    python manage.py lms export_tenant_services MIT --db credentials --scope scope.json --out-dir ./out
    python manage.py lms export_tenant_services MIT --db ecommerce --scope scope.json --out-dir ./out --dry-run
"""
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from openedx.features.edly.tenant_export import dbutil, manifest as manifest_mod, resume, services
from openedx.features.edly.tenant_export import secrets as secrets_mod
from openedx.features.edly.tenant_export.scope import load_scope_file
from openedx.features.edly.tenant_export.sqlutil import count_rows


class Command(BaseCommand):
    help = (
        "Dump one tenant's credentials/discovery/ecommerce tables into --out-dir "
        "(flat <db>__<table>.sql layout). DB connection: LMS default settings with "
        "NAME=<db>, overridable via the EXPORT_TENANT_DATABASES setting."
    )

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument('--db', required=True, choices=services.SERVICE_DBS)
        parser.add_argument('--scope', required=True, help='Path to scope.json (see export_tenant_scope --services).')
        parser.add_argument('--out-dir', required=True, help='Directory to write dump files + MANIFEST.json into.')
        parser.add_argument('--tables', help="Comma-separated subset of the db's table list; never a DENYLIST table.")
        parser.add_argument(
            '--dry-run', action='store_true',
            help='SELECT COUNT(*) per table instead of dumping; writes no .sql files.',
        )

    def handle(self, *args, **options):
        os.umask(0o077)  # every file this command writes is learner/customer PII

        scope_data = load_scope_file(options['scope'])
        slug = scope_data['slug']
        if options['slug'] != slug:
            raise CommandError(f"slug {options['slug']!r} does not match scope.json's slug {slug!r}")

        spec = services.get_spec(options['db'])
        ctx = scope_data.get('services', {}).get(spec.name)
        if not ctx:
            raise CommandError(
                f"scope.json has no services.{spec.name} block -- run export_tenant_scope --services {spec.name}"
            )

        requested = (
            [t.strip() for t in options['tables'].split(',') if t.strip()] if options['tables'] else list(spec.tables)
        )
        try:
            secrets_mod.check_denylist(requested)
        except secrets_mod.DenylistedTableError as exc:
            raise CommandError(str(exc))
        unknown = set(requested) - set(spec.tables)
        if unknown:
            raise CommandError(f"unknown/out-of-scope {spec.name} tables requested: {sorted(unknown)}")

        out_dir = Path(options['out_dir'])
        out_dir.mkdir(parents=True, exist_ok=True)
        db_connection = services.service_connection(spec.name)
        conn_params = dbutil.conn_params_from_settings_dict(db_connection.settings_dict)

        scope_sha = manifest_mod.sha256_of_text(Path(options['scope']).read_text())
        mf = manifest_mod.Manifest(out_dir / "MANIFEST.json", slug, scope_sha, spec.expected_stems)
        mf.data.setdefault("services", {})[spec.name] = ctx
        for table, (status, reason) in spec.excluded.items():
            key = services.stem(spec.name, table)
            if not options['dry_run'] and key not in mf.data["tables"]:
                mf.update_table(key, status=status, reason=reason, db=spec.name)

        with db_connection.cursor() as cursor:
            for table in requested:
                key = services.stem(spec.name, table)
                if resume.is_done(out_dir, slug, key):
                    self.stdout.write(f"skip (done): {key}")
                    continue
                if not dbutil.table_exists(cursor, table):
                    # A spec table missing from a service DB is a wrong schema guess, not "nothing to dump".
                    msg = "table not in source DB (spec/schema mismatch)"
                    if not options['dry_run']:
                        mf.update_table(key, status="error", db=spec.name, error=msg)
                    self.stderr.write(self.style.ERROR(f"ERROR {key}: {msg}"))
                    continue

                where = spec.where(table, ctx)
                if options['dry_run']:
                    self.stdout.write(f"[dry-run] {key}: {count_rows(cursor, table, where)} rows")
                    continue

                out_path = out_dir / f"{key}.sql"
                partial_path = out_dir / f"{key}.sql.partial"
                if partial_path.exists():
                    partial_path.unlink()

                try:
                    secret_cols = spec.secret_columns.get(table)
                    if secret_cols:
                        rows = dbutil.dump_redacted_table(
                            conn_params, table, where, partial_path, cursor, secret_cols=secret_cols,
                        )
                    else:
                        dbutil.dump_table_chunked(conn_params, table, partial_path, [where])
                        rows = count_rows(cursor, table, where)

                    os.replace(partial_path, out_path)
                    mf.update_table(
                        key, status="complete", db=spec.name, rows=rows, sha256=dbutil.sha256_file(out_path),
                        redacted_columns=sorted(secret_cols or []),
                        redaction="blank_whole_column" if secret_cols else None,
                        where_sha256=manifest_mod.sha256_of_text(where),
                    )
                    resume.mark_done(out_dir, slug, key)
                    self.stdout.write(f"dumped {key}: {rows} rows")
                except Exception as exc:  # pylint: disable=broad-except -- one bad table must not abort the whole run
                    err = dbutil.describe_error(exc)
                    mf.update_table(key, status="error", db=spec.name, error=err)
                    self.stderr.write(self.style.ERROR(f"ERROR dumping {key}: {err}"))

        if options['dry_run']:
            return  # dry-run must not persist MANIFEST.json
        self.stdout.write(f"manifest status: {mf.finalize()}")

