"""
Finalize one Edly tenant's export run: re-verify on-disk checksums for every
table a prior `export_tenant_mysql`/`export_tenant_csmh` run claimed
"complete", and (re)compute MANIFEST.json's overall `status` -- the one
field to check before trusting a run. Part of the MIT off-boarding export
tooling (EDLYPRODUCT-8584 Phase 1).

Usage:
    python manage.py lms export_tenant_package MIT --out-dir ./out
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from openedx.features.edly.tenant_export import dbutil, manifest as manifest_mod, services, tables


class Command(BaseCommand):
    help = "Verify checksums and finalize MANIFEST.json's overall status for a tenant export run."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument(
            '--out-dir', required=True, help='Directory produced by export_tenant_mysql/export_tenant_csmh.',
        )
        parser.add_argument(
            '--skip-db', action='append', default=[], choices=services.SERVICE_DBS,
            help='Service db deliberately NOT exported (repeatable); without it, package is only '
                 '"complete" once credentials, discovery and ecommerce all ran.',
        )

    def handle(self, *args, **options):
        out_dir = Path(options['out_dir'])
        manifest_path = out_dir / "MANIFEST.json"
        if not manifest_path.exists():
            raise CommandError(f"no MANIFEST.json in {out_dir} -- run export_tenant_mysql/export_tenant_csmh first")

        data = json.loads(manifest_path.read_text())
        if options['slug'] != data['tenant_slug']:
            self.stderr.write(self.style.WARNING(
                f"slug {options['slug']!r} does not match manifest's tenant_slug {data['tenant_slug']!r}"
            ))

        skipped = set(options['skip_db'])
        mf = manifest_mod.Manifest(
            manifest_path, data['tenant_slug'], data['scope_sha256'],
            list(tables.EXPECTED_TABLES) + services.expected_service_stems(skipped),
        )
        # The manifest unions persisted expectations; an explicit opt-out must also drop those.
        for db in skipped:
            mf.expected_tables -= set(services.get_spec(db).expected_stems)
        mf.data['expected_tables'] = sorted(mf.expected_tables)
        mf.data['skipped_dbs'] = sorted(skipped)

        # Re-verify on-disk checksums for every table claimed "complete" --
        # catches post-dump truncation/corruption before calling a run
        # trustworthy.
        problems = []
        for table, entry in list(mf.data['tables'].items()):
            if table == tables.OLX_KEY or entry.get('status') != 'complete':
                continue
            sql_path = out_dir / f"{table}.sql"
            if not sql_path.exists():
                problems.append(table)
                entry['status'] = 'error'
                entry['error'] = 'file missing at package time'
                continue
            actual_sha = dbutil.sha256_file(sql_path)
            if actual_sha != entry.get('sha256'):
                problems.append(table)
                entry['status'] = 'error'
                entry['error'] = 'sha256 mismatch at package time'

        status = mf.finalize()
        self.stdout.write(f"manifest status: {status}")
        if problems:
            self.stderr.write(self.style.WARNING(f"checksum/file problems for: {problems}"))

        errored = sorted(t for t, e in mf.data['tables'].items() if e.get('status') == 'error')
        if errored:
            self.stderr.write(self.style.WARNING(f"tables/steps with errors (review before handoff): {errored}"))

        if status not in ('complete', 'complete_with_errors'):
            raise CommandError(f"export is not ready to hand off -- manifest status is {status!r}")
