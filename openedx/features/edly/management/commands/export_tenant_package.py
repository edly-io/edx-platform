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

from openedx.features.edly.tenant_export import manifest as manifest_mod, services, tables


class Command(BaseCommand):
    help = "Verify checksums and finalize MANIFEST.json's overall status for a tenant export run."

    def add_arguments(self, parser):
        parser.add_argument('slug', help='EdlySubOrganization slug identifying the tenant to export.')
        parser.add_argument(
            '--out-dir', required=True, help='Directory produced by the export_tenant_* commands.',
        )
        parser.add_argument(
            '--skip-db', action='append', default=[], choices=services.SERVICE_DBS,
            help='Service db deliberately NOT exported (repeatable); without it, package is only '
                 '"complete" once credentials, discovery, ecommerce and notes all ran.',
        )
        parser.add_argument(
            '--skip-forum', action='store_true',
            help='Forum deliberately NOT exported; without it, package is only "complete" once export_tenant_forum ran.',
        )
        parser.add_argument(
            '--skip-s3', action='store_true',
            help='S3 assets deliberately NOT copied; without it, package is only "complete" once '
                 'export_tenant_s3 ran for every bucket.',
        )

        parser.add_argument(
            '--allow-errors', action='store_true',
            help='Exit 0 even when the manifest status is "complete_with_errors" (default: exit non-zero).',
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
            list(tables.EXPECTED_TABLES) + services.expected_service_stems(skipped)
            + tables.phase3_expected(options['skip_forum'], options['skip_s3']),
        )
        # The manifest unions persisted expectations; an explicit opt-out must also drop those.
        for db in skipped:
            mf.expected_tables -= set(services.get_spec(db).expected_stems)
            mf.expected_excluded -= {services.stem(db, t) for t in services.get_spec(db).excluded}
        if options['skip_forum']:
            mf.expected_tables -= set(tables.FORUM_KEYS)
        if options['skip_s3']:
            mf.expected_tables -= set(tables.S3_KEYS)
        mf.data['expected_tables'] = sorted(mf.expected_tables)
        mf.data['expected_excluded'] = sorted(mf.expected_excluded)
        mf.data['skipped_dbs'] = sorted(skipped)
        mf.data['skipped_phase3'] = sorted(
            n for n, skip in (('forum', options['skip_forum']), ('s3', options['skip_s3'])) if skip
        )

        # Re-verify on-disk checksums for every table claimed "complete" --
        # catches post-dump truncation/corruption before calling a run
        # trustworthy.
        problems = manifest_mod.verify_entry_files(mf.data, out_dir, skip_keys=(tables.OLX_KEY,))

        status = mf.finalize()
        self.stdout.write(f"manifest status: {status}")
        if problems:
            self.stderr.write(self.style.WARNING(f"checksum/file problems for: {problems}"))

        errored = sorted(t for t, e in mf.data['tables'].items() if e.get('status') == 'error')
        if errored:
            self.stderr.write(self.style.WARNING(f"tables/steps with errors (review before handoff): {errored}"))

        if status == 'complete_with_errors' and not options['allow_errors']:
            raise CommandError(
                "manifest status is 'complete_with_errors' -- fix and re-run the failed steps, "
                "or pass --allow-errors to accept"
            )
        if status not in ('complete', 'complete_with_errors'):
            raise CommandError(f"export is not ready to hand off -- manifest status is {status!r}")
