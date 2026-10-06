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

from openedx.features.edly.tenant_export import dbutil, manifest as manifest_mod, tables


class Command(BaseCommand):
    help = "Verify checksums and finalize MANIFEST.json's overall status for a tenant export run."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument(
            '--out-dir', required=True, help='Directory produced by export_tenant_mysql/export_tenant_csmh.',
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

        mf = manifest_mod.Manifest(manifest_path, data['tenant_slug'], data['scope_sha256'], tables.EXPECTED_TABLES)

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
